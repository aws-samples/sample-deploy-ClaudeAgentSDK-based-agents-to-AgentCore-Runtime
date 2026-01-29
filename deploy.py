"""
Deploy agent to AWS Bedrock AgentCore Runtime
"""

import json
import time
import boto3
from boto3.session import Session
from bedrock_agentcore_starter_toolkit import Runtime

# Claude Code CLI configuration to inject into Dockerfile
CLAUDE_CODE_DOCKERFILE_PATCH = """
# Install system dependencies for Claude Code CLI
RUN apt-get update && apt-get install -y \\
    curl \\
    git \\
    nodejs \\
    npm \\
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code

# Claude Code Bedrock configuration
ENV CLAUDE_CODE_USE_BEDROCK=1 \\
    ANTHROPIC_MODEL=us.anthropic.claude-sonnet-4-20250514-v1:0
"""


def patch_dockerfile():
    """Patch the Dockerfile to include Claude Code CLI and Bedrock configuration."""
    dockerfile_path = "Dockerfile"

    with open(dockerfile_path, "r") as f:
        content = f.read()

    # Check if already patched
    if "CLAUDE_CODE_USE_BEDROCK" in content:
        print("Dockerfile already contains Claude Code configuration.")
        return

    # Find insertion point (after the first ENV block)
    lines = content.split("\n")
    insert_index = 0

    for i, line in enumerate(lines):
        if line.startswith("ENV ") and "AWS_REGION" in content[: content.find(line) + len(line) + 100]:
            # Find the end of this ENV block
            j = i + 1
            while j < len(lines) and (lines[j].startswith("    ") or lines[j].strip() == ""):
                j += 1
            insert_index = j
            break

    if insert_index == 0:
        # Fallback: insert after WORKDIR
        for i, line in enumerate(lines):
            if line.startswith("WORKDIR"):
                insert_index = i + 1
                break

    # Insert the patch
    lines.insert(insert_index, CLAUDE_CODE_DOCKERFILE_PATCH)

    with open(dockerfile_path, "w") as f:
        f.write("\n".join(lines))

    print("Dockerfile patched with Claude Code CLI configuration.")


def deploy():
    """Deploy the agent to AgentCore Runtime."""
    boto_session = Session()
    region = boto_session.region_name

    # Step 1: Configure runtime (generates Dockerfile)
    print("Step 1: Configuring runtime...")
    agentcore_runtime = Runtime()
    agentcore_runtime.configure(
        entrypoint="agent.py",
        auto_create_execution_role=True,
        auto_create_ecr=True,
        requirements_file="requirements.txt",
        region=region,
        agent_name="conversation_agent_claudeagentsdk",
    )

    # Step 2: Patch Dockerfile with Claude Code CLI
    print("Step 2: Patching Dockerfile...")
    patch_dockerfile()

    # Step 3: Launch deployment
    print("Step 3: Launching agent to AgentCore Runtime...")
    launch_result = agentcore_runtime.launch()
    print(f"Agent ID: {launch_result.agent_id}")
    print(f"ECR URI: {launch_result.ecr_uri}")

    # Step 4: Wait for deployment
    print("Step 4: Waiting for deployment to complete...")
    status = agentcore_runtime.status().endpoint["status"]
    while status not in ["READY", "CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"]:
        time.sleep(10)
        status = agentcore_runtime.status().endpoint["status"]
        print(f"Status: {status}")

    if status == "READY":
        print("Deployment successful!")
        return launch_result, agentcore_runtime
    else:
        print(f"Deployment failed: {status}")
        return None, None


def invoke(agentcore_runtime: Runtime, prompt: str):
    """Invoke the deployed agent using Runtime toolkit."""
    response = agentcore_runtime.invoke({"prompt": prompt})
    return response


def cleanup(agent_id: str, ecr_uri: str):
    """Delete agent and ECR repository."""
    region = Session().region_name
    control_client = boto3.client("bedrock-agentcore-control", region_name=region)
    ecr_client = boto3.client("ecr", region_name=region)

    print(f"Deleting agent {agent_id}...")
    control_client.delete_agent_runtime(agentRuntimeId=agent_id)

    repo_name = ecr_uri.split("/")[1]
    print(f"Deleting ECR repository {repo_name}...")
    ecr_client.delete_repository(repositoryName=repo_name, force=True)

    print("Cleanup complete.")


if __name__ == "__main__":
    result, runtime = deploy()
    if result:
        print(f"\nTest invocation:")
        response = invoke(runtime, "Hello, how are you?")
        print(f"Response: {response}")
