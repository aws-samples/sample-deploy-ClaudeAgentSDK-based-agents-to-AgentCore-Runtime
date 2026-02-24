"""
Manual deployment to AWS Bedrock AgentCore Runtime (without starter toolkit)

This script directly builds, pushes to ECR, and deploys to AgentCore.
"""

import json
import time
import subprocess
import boto3
from boto3.session import Session


class ManualDeployer:
    """Deploy agent to AgentCore without using starter toolkit."""

    def __init__(self, agent_name: str, region: str = None):
        self.agent_name = agent_name
        self.session = Session()
        self.region = region or self.session.region_name
        self.account_id = boto3.client("sts").get_caller_identity()["Account"]

        # AWS clients
        self.ecr_client = boto3.client("ecr", region_name=self.region)
        self.iam_client = boto3.client("iam", region_name=self.region)
        self.agentcore_client = boto3.client("bedrock-agentcore-control", region_name=self.region)

        # Resource names
        self.ecr_repo_name = f"agentcore/{agent_name}"
        self.role_name = f"AgentCoreExecutionRole-{agent_name}"
        self.image_tag = "latest"

    def create_ecr_repository(self) -> str:
        """Create ECR repository if not exists."""
        print(f"Creating ECR repository: {self.ecr_repo_name}")

        try:
            response = self.ecr_client.create_repository(
                repositoryName=self.ecr_repo_name,
                imageScanningConfiguration={"scanOnPush": True},
            )
            repo_uri = response["repository"]["repositoryUri"]
            print(f"ECR repository created: {repo_uri}")
        except self.ecr_client.exceptions.RepositoryAlreadyExistsException:
            response = self.ecr_client.describe_repositories(
                repositoryNames=[self.ecr_repo_name]
            )
            repo_uri = response["repositories"][0]["repositoryUri"]
            print(f"ECR repository already exists: {repo_uri}")

        return repo_uri

    def build_and_push_image_local(self, ecr_uri: str) -> str:
        """Build Docker image locally and push to ECR (requires local Docker)."""
        image_uri = f"{ecr_uri}:{self.image_tag}"

        # Get ECR login
        print("Authenticating with ECR...")
        auth_response = self.ecr_client.get_authorization_token()
        auth_data = auth_response["authorizationData"][0]
        registry = auth_data["proxyEndpoint"]

        # Docker login
        password = subprocess.run(
            ["aws", "ecr", "get-login-password", "--region", self.region],
            check=True, capture_output=True, text=True
        ).stdout
        subprocess.run(
            ["docker", "login", "--username", "AWS", "--password-stdin", registry],
            input=password, check=True, capture_output=True, text=True
        )

        # Build image
        print("Building Docker image...")
        subprocess.run(["docker", "build", "-t", image_uri, "."], check=True)

        # Push image
        print(f"Pushing image to ECR: {image_uri}")
        subprocess.run(["docker", "push", image_uri], check=True)

        return image_uri

    def build_and_push_image_codebuild(self, ecr_uri: str) -> str:
        """Build Docker image using AWS CodeBuild (no local Docker needed)."""
        import zipfile
        import os

        image_uri = f"{ecr_uri}:{self.image_tag}"
        project_name = f"agentcore-build-{self.agent_name}"
        s3_bucket = f"agentcore-build-{self.account_id}-{self.region}"

        s3_client = boto3.client("s3", region_name=self.region)
        codebuild_client = boto3.client("codebuild", region_name=self.region)

        # Step 1: Create S3 bucket for source code
        print(f"Creating S3 bucket: {s3_bucket}")
        try:
            if self.region == "us-east-1":
                s3_client.create_bucket(Bucket=s3_bucket)
            else:
                s3_client.create_bucket(
                    Bucket=s3_bucket,
                    CreateBucketConfiguration={"LocationConstraint": self.region},
                )
        except s3_client.exceptions.BucketAlreadyOwnedByYou:
            pass

        # Step 2: Create buildspec.yml (simplified for ARM64 native build)
        buildspec = f"""version: 0.2
phases:
  pre_build:
    commands:
      - echo Logging in to Amazon ECR...
      - aws ecr get-login-password --region {self.region} | docker login --username AWS --password-stdin {ecr_uri.split('/')[0]}
  build:
    commands:
      - echo Building Docker image...
      - docker build -t {image_uri} .
  post_build:
    commands:
      - echo Pushing Docker image...
      - docker push {image_uri}
"""
        with open("buildspec.yml", "w") as f:
            f.write(buildspec)

        # Step 3: Zip source code
        print("Packaging source code...")
        zip_file = "source.zip"
        with zipfile.ZipFile(zip_file, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in ["Dockerfile", "requirements.txt", "agent.py", "buildspec.yml"]:
                if os.path.exists(file):
                    zf.write(file)

        # Step 4: Upload to S3
        s3_key = f"{self.agent_name}/source.zip"
        print(f"Uploading source to S3: s3://{s3_bucket}/{s3_key}")
        s3_client.upload_file(zip_file, s3_bucket, s3_key)

        # Step 5: Create CodeBuild service role
        codebuild_role_arn = self._create_codebuild_role()

        # Step 6: Create CodeBuild project
        print(f"Creating CodeBuild project: {project_name}")
        try:
            codebuild_client.create_project(
                name=project_name,
                source={
                    "type": "S3",
                    "location": f"{s3_bucket}/{s3_key}",
                },
                artifacts={"type": "NO_ARTIFACTS"},
                environment={
                    "type": "ARM_CONTAINER",  # Changed to ARM64
                    "computeType": "BUILD_GENERAL1_SMALL",
                    "image": "aws/codebuild/amazonlinux2-aarch64-standard:3.0",  # ARM64 image
                    "privilegedMode": True,  # Required for Docker
                },
                serviceRole=codebuild_role_arn,
            )
        except codebuild_client.exceptions.ResourceAlreadyExistsException:
            codebuild_client.update_project(
                name=project_name,
                source={
                    "type": "S3",
                    "location": f"{s3_bucket}/{s3_key}",
                },
                environment={
                    "type": "ARM_CONTAINER",  # Changed to ARM64
                    "computeType": "BUILD_GENERAL1_SMALL",
                    "image": "aws/codebuild/amazonlinux2-aarch64-standard:3.0",  # ARM64 image
                    "privilegedMode": True,  # Required for Docker
                },
            )

        # Step 7: Start build
        print("Starting CodeBuild...")
        build_response = codebuild_client.start_build(projectName=project_name)
        build_id = build_response["build"]["id"]
        print(f"Build ID: {build_id}")

        # Step 8: Wait for build to complete
        print("Waiting for build to complete...")
        while True:
            build_info = codebuild_client.batch_get_builds(ids=[build_id])
            status = build_info["builds"][0]["buildStatus"]
            print(f"Build status: {status}")

            if status == "SUCCEEDED":
                print("Build successful!")
                break
            elif status in ["FAILED", "FAULT", "STOPPED", "TIMED_OUT"]:
                raise Exception(f"Build failed with status: {status}")

            time.sleep(10)

        # Cleanup
        os.remove("buildspec.yml")
        os.remove(zip_file)

        return image_uri

    def _create_codebuild_role(self) -> str:
        """Create IAM role for CodeBuild."""
        role_name = f"CodeBuildRole-{self.agent_name}"

        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "codebuild.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }

        try:
            response = self.iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
            )
            role_arn = response["Role"]["Arn"]

            # Attach policies
            policies = [
                "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser",
                "arn:aws:iam::aws:policy/AmazonS3FullAccess",  # Changed from ReadOnly
                "arn:aws:iam::aws:policy/CloudWatchLogsFullAccess",
            ]
            for policy in policies:
                self.iam_client.attach_role_policy(RoleName=role_name, PolicyArn=policy)

            print(f"Created CodeBuild role: {role_arn}")
            time.sleep(10)  # Wait for role propagation

        except self.iam_client.exceptions.EntityAlreadyExistsException:
            response = self.iam_client.get_role(RoleName=role_name)
            role_arn = response["Role"]["Arn"]

        return role_arn

    def build_and_push_image(self, ecr_uri: str, use_codebuild: bool = False) -> str:
        """Build and push Docker image."""
        if use_codebuild:
            return self.build_and_push_image_codebuild(ecr_uri)
        else:
            return self.build_and_push_image_local(ecr_uri)

    def create_execution_role(self) -> str:
        """Create IAM execution role for AgentCore."""
        print(f"Creating IAM role: {self.role_name}")

        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }

        try:
            response = self.iam_client.create_role(
                RoleName=self.role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="Execution role for Bedrock AgentCore Runtime",
            )
            role_arn = response["Role"]["Arn"]
            print(f"IAM role created: {role_arn}")

            # Attach policies
            policies = [
                "arn:aws:iam::aws:policy/AmazonBedrockFullAccess",
                "arn:aws:iam::aws:policy/CloudWatchLogsFullAccess",
                "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
            ]
            for policy in policies:
                self.iam_client.attach_role_policy(
                    RoleName=self.role_name, PolicyArn=policy
                )
                print(f"Attached policy: {policy}")

            # Wait for role to propagate
            print("Waiting for IAM role to propagate...")
            time.sleep(10)

        except self.iam_client.exceptions.EntityAlreadyExistsException:
            response = self.iam_client.get_role(RoleName=self.role_name)
            role_arn = response["Role"]["Arn"]
            print(f"IAM role already exists: {role_arn}")

        return role_arn

    def create_agent_runtime(self, image_uri: str, role_arn: str) -> dict:
        """Create or update AgentCore Runtime."""
        print(f"Creating AgentCore Runtime: {self.agent_name}")

        try:
            response = self.agentcore_client.create_agent_runtime(
                agentRuntimeName=self.agent_name,
                agentRuntimeArtifact={
                    "containerConfiguration": {
                        "containerUri": image_uri,
                    }
                },
                roleArn=role_arn,
                networkConfiguration={
                    "networkMode": "PUBLIC",
                },
            )

            agent_id = response["agentRuntimeId"]
            agent_arn = response["agentRuntimeArn"]
            print(f"AgentCore Runtime created:")
            print(f"  ID: {agent_id}")
            print(f"  ARN: {agent_arn}")

            return {
                "agent_id": agent_id,
                "agent_arn": agent_arn,
                "image_uri": image_uri,
                "role_arn": role_arn,
            }

        except self.agentcore_client.exceptions.ConflictException:
            # Runtime already exists, update it
            print(f"AgentCore Runtime already exists, updating...")

            # First, get the existing runtime ID
            list_response = self.agentcore_client.list_agent_runtimes()
            agent_id = None
            for runtime in list_response.get("agentRuntimes", []):
                if runtime["agentRuntimeName"] == self.agent_name:
                    agent_id = runtime["agentRuntimeId"]
                    break

            if not agent_id:
                raise Exception("Runtime exists but could not find its ID")

            # Update the runtime
            response = self.agentcore_client.update_agent_runtime(
                agentRuntimeId=agent_id,
                agentRuntimeArtifact={
                    "containerConfiguration": {
                        "containerUri": image_uri,
                    }
                },
                roleArn=role_arn,
                networkConfiguration={
                    "networkMode": "PUBLIC",
                },
            )

            agent_arn = response["agentRuntimeArn"]
            print(f"AgentCore Runtime updated:")
            print(f"  ID: {agent_id}")
            print(f"  ARN: {agent_arn}")

            return {
                "agent_id": agent_id,
                "agent_arn": agent_arn,
                "image_uri": image_uri,
                "role_arn": role_arn,
            }

        except Exception as e:
            print(f"Error creating/updating AgentCore Runtime: {e}")
            raise

    def wait_for_ready(self, agent_id: str, timeout: int = 600) -> str:
        """Wait for AgentCore Runtime to be ready."""
        print("Waiting for AgentCore Runtime to be ready...")

        end_statuses = ["READY", "CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"]
        start_time = time.time()

        while time.time() - start_time < timeout:
            response = self.agentcore_client.get_agent_runtime(agentRuntimeId=agent_id)
            status = response["status"]
            print(f"Status: {status}")

            if status in end_statuses:
                return status

            time.sleep(10)

        raise TimeoutError("AgentCore Runtime did not become ready in time")

    def deploy(self, use_codebuild: bool = False) -> dict:
        """
        Full deployment workflow.

        Args:
            use_codebuild: If True, use AWS CodeBuild (no local Docker needed).
                          If False, use local Docker.
        """
        print("=" * 60)
        print(f"Deploying agent: {self.agent_name}")
        print(f"Region: {self.region}")
        print(f"Account: {self.account_id}")
        print(f"Build method: {'CodeBuild' if use_codebuild else 'Local Docker'}")
        print("=" * 60)

        # Step 1: Create ECR repository
        ecr_uri = self.create_ecr_repository()

        # Step 2: Build and push Docker image
        image_uri = self.build_and_push_image(ecr_uri, use_codebuild=use_codebuild)

        # Step 3: Create IAM execution role
        role_arn = self.create_execution_role()

        # Step 4: Create AgentCore Runtime
        result = self.create_agent_runtime(image_uri, role_arn)

        # Step 5: Wait for ready
        status = self.wait_for_ready(result["agent_id"])

        if status == "READY":
            print("\nDeployment successful!")
            return result
        else:
            print(f"\nDeployment failed with status: {status}")
            return None

    def invoke(self, agent_arn: str, prompt: str) -> dict:
        """Invoke the deployed agent."""
        runtime_client = boto3.client("bedrock-agentcore", region_name=self.region)

        response = runtime_client.invoke_agent_runtime(
            agentRuntimeArn=agent_arn,
            qualifier="DEFAULT",
            payload=json.dumps({"prompt": prompt}),
        )

        # Read the streaming response body
        if "response" in response:
            stream_body = response["response"]
            response_text = stream_body.read().decode("utf-8")
            response["response"] = json.loads(response_text)

        return response

    def cleanup(self, agent_id: str):
        """Delete all resources."""
        print("Cleaning up resources...")

        # Delete AgentCore Runtime
        try:
            self.agentcore_client.delete_agent_runtime(agentRuntimeId=agent_id)
            print(f"Deleted AgentCore Runtime: {agent_id}")
        except Exception as e:
            print(f"Error deleting runtime: {e}")

        # Delete ECR repository
        try:
            self.ecr_client.delete_repository(
                repositoryName=self.ecr_repo_name, force=True
            )
            print(f"Deleted ECR repository: {self.ecr_repo_name}")
        except Exception as e:
            print(f"Error deleting ECR repo: {e}")

        # Delete IAM role
        try:
            # Detach policies first
            policies = self.iam_client.list_attached_role_policies(RoleName=self.role_name)
            for policy in policies["AttachedPolicies"]:
                self.iam_client.detach_role_policy(
                    RoleName=self.role_name, PolicyArn=policy["PolicyArn"]
                )
            self.iam_client.delete_role(RoleName=self.role_name)
            print(f"Deleted IAM role: {self.role_name}")
        except Exception as e:
            print(f"Error deleting IAM role: {e}")

        print("Cleanup complete.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Deploy agent to AgentCore Runtime")
    parser.add_argument("--name", default="conversation_agent_claudeagentsdk", help="Agent name")
    parser.add_argument("--codebuild", action="store_true", help="Use AWS CodeBuild (no local Docker needed)")
    parser.add_argument("--cleanup", help="Cleanup agent by ID")
    args = parser.parse_args()

    deployer = ManualDeployer(agent_name=args.name)

    if args.cleanup:
        deployer.cleanup(args.cleanup)
    else:
        result = deployer.deploy(use_codebuild=args.codebuild)

        if result:
            print(f"\nTest invocation:")
            response = deployer.invoke(result["agent_arn"], "Hello, how are you?")
            print(f"Response: {response}")
