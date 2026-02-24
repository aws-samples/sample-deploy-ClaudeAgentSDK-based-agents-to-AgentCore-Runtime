"""
Test script for AgentCore Runtime session persistence
Tests session memory and persistence across different session IDs

Usage:
    # Option 1: Use environment variable
    export AGENT_RUNTIME_ID=your-agent-runtime-id
    export AWS_REGION=us-east-1
    python test_runtime.py

    # Option 2: Use command line arguments
    python test_runtime.py --agent-id your-agent-runtime-id --region us-east-1

    # Option 3: Run 15-minute timeout test
    python test_runtime.py --agent-id your-agent-runtime-id --test-timeout

    # Option 4: Custom quick test
    python test_runtime.py --agent-id your-agent-runtime-id --custom
"""

import os
import time
import json
import uuid
from datetime import datetime, timezone
import boto3
from boto3.session import Session


class RuntimeTester:
    """Test runner for AgentCore Runtime with session management."""

    @staticmethod
    def generate_session_id(prefix: str = "test-session") -> str:
        """Generate a valid session ID (min 33 characters)."""
        return f"{prefix}-{uuid.uuid4()}"

    def __init__(self, agent_id: str = None, region: str = None):
        """Initialize the tester with agent ID and region."""
        # Get agent ID from parameter, environment, or fail
        self.agent_id = agent_id or os.environ.get("AGENT_RUNTIME_ID")
        if not self.agent_id:
            print("\n[ERROR] Agent Runtime ID not provided.")
            print("\n[SOLUTION] Provide agent ID via:")
            print("    1. Command line: python test_runtime.py --agent-id YOUR_AGENT_ID")
            print("    2. Environment: export AGENT_RUNTIME_ID=YOUR_AGENT_ID")
            print()
            raise ValueError("Agent Runtime ID required")

        # Get region from parameter, environment, or session
        self.boto_session = Session()
        self.region = region or os.environ.get("AWS_REGION") or self.boto_session.region_name
        if not self.region:
            print("\n[WARNING] AWS region not set, defaulting to us-east-1")
            self.region = "us-east-1"

        self.control_client = boto3.client("bedrock-agentcore-control", region_name=self.region)
        self.runtime_client = boto3.client("bedrock-agentcore", region_name=self.region)

        # Get agent ARN from agent ID
        try:
            response = self.control_client.get_agent_runtime(agentRuntimeId=self.agent_id)
            self.agent_arn = response.get("agentRuntimeArn")
            print(f"[INFO] Agent Runtime ID: {self.agent_id}")
            print(f"[INFO] Agent Runtime ARN: {self.agent_arn}")
            print(f"[INFO] Region: {self.region}")
        except Exception as e:
            print(f"\n[ERROR] Could not get agent runtime info: {e}")
            print("\n[SOLUTION] Check that:")
            print(f"    1. Agent Runtime ID '{self.agent_id}' exists")
            print(f"    2. You have access to region '{self.region}'")
            print(f"    3. AWS credentials are configured correctly")
            raise

    def invoke_with_session(self, prompt: str, session_id: str) -> dict:
        """
        Invoke the agent with a specific session ID.
        Prints invocation timestamp and returns response with timestamps.
        """
        invocation_timestamp = datetime.now(timezone.utc).isoformat()
        print(f"\n{'='*60}")
        print(f"[CLIENT] Invocation initiated at: {invocation_timestamp}")
        print(f"[CLIENT] Session ID: {session_id}")
        print(f"[CLIENT] Prompt: {prompt}")
        print(f"{'='*60}")

        if not self.agent_id:
            print("[ERROR] No agent_id configured. Cannot invoke.")
            return None

        try:
            # Invoke using bedrock-agentcore client
            response = self.runtime_client.invoke_agent_runtime(
                agentRuntimeArn=self.agent_arn,
                qualifier="DEFAULT",
                runtimeSessionId=session_id,
                payload=json.dumps({"prompt": prompt}),
            )

            # Read the streaming response body
            response_data = {}
            if "response" in response:
                stream_body = response["response"]
                response_text_raw = stream_body.read().decode("utf-8")
                response_data = json.loads(response_text_raw)

            # Extract response information
            output = response_data.get("output", {})
            message = output.get("message", {})
            content = message.get("content", [])

            # Get timestamps from response
            request_timestamp = output.get("request_timestamp", "N/A")
            response_timestamp = output.get("response_timestamp", "N/A")

            # Extract text response
            response_text = ""
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    response_text += item["text"]

            print(f"\n[AGENT] Request received at: {request_timestamp}")
            print(f"[AGENT] Response sent at: {response_timestamp}")
            print(f"[AGENT] Response: {response_text}\n")

            return {
                "invocation_timestamp": invocation_timestamp,
                "request_timestamp": request_timestamp,
                "response_timestamp": response_timestamp,
                "response_text": response_text,
                "full_response": response_data
            }

        except Exception as e:
            print(f"[ERROR] Invocation failed: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
            return None

    def stop_session(self, session_id: str):
        """
        Stop a runtime session using the stop_runtime_session API.

        Args:
            session_id: The identifier for the session to terminate

        Returns:
            dict: Response containing runtimeSessionId and statusCode, or None on error
        """
        print(f"\n[CLIENT] Stopping session: {session_id}")

        if not self.agent_arn:
            print("[ERROR] No agent ARN configured. Cannot stop session.")
            return None

        try:
            response = self.runtime_client.stop_runtime_session(
                agentRuntimeArn=self.agent_arn,
                runtimeSessionId=session_id,
                qualifier="DEFAULT"
            )

            status_code = response.get("statusCode")
            returned_session_id = response.get("runtimeSessionId")

            print(f"[CLIENT] Session stopped successfully")
            print(f"[CLIENT] Status Code: {status_code}")
            print(f"[CLIENT] Session ID: {returned_session_id}")

            return response

        except Exception as e:
            print(f"[ERROR] Failed to stop session: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
            return None

    def wait_minutes(self, minutes: int):
        """Wait for specified minutes with countdown."""
        print(f"\n[CLIENT] Waiting {minutes} minute(s)...")
        total_seconds = minutes * 60
        for remaining in range(total_seconds, 0, -30):
            mins, secs = divmod(remaining, 60)
            print(f"[CLIENT] Time remaining: {mins:02d}:{secs:02d}", end='\r')
            time.sleep(min(30, remaining))
        print(f"\n[CLIENT] Wait complete\n")


def run_test_scenario(agent_id: str = None, region: str = None):
    """
    Main test scenario:
    1. Session A: Introduce user identity
    2. Session B: Test if agent remembers (should not)
    3. Session A (within 15 min): Test if agent remembers (should remember)
    4. Stop Session A, restart within 15 min: Test persistence after stop
    """

    print("="*60)
    print("AgentCore Runtime Session Persistence Test")
    print("="*60)

    # Initialize tester
    tester = RuntimeTester(agent_id=agent_id, region=region)

    if not tester.agent_id:
        print("ERROR: No agent found. Please deploy the agent first.")
        return

    print(f"\nTesting Agent ID: {tester.agent_id}")
    print(f"Region: {tester.region}\n")

    # Define session IDs (must be at least 33 characters)
    SESSION_A = tester.generate_session_id("session-A")
    SESSION_B = tester.generate_session_id("session-B")

    print(f"Session A ID: {SESSION_A}")
    print(f"Session B ID: {SESSION_B}\n")

    # Test 1: Session A - Introduce identity
    print("\n" + "="*60)
    print("TEST 1: Session A - Introduce user identity")
    print("="*60)
    result_1 = tester.invoke_with_session(
        prompt="你好! 我叫张三, 来自ABC科技公司。",
        session_id=SESSION_A
    )

    if not result_1:
        print("TEST 1 FAILED: Could not complete invocation")
        return

    time.sleep(2)  # Brief pause between tests

    # Test 2: Session B - Ask if agent knows who the user is
    print("\n" + "="*60)
    print("TEST 2: Session B - Test cross-session memory (should NOT remember)")
    print("="*60)
    result_2 = tester.invoke_with_session(
        prompt="你知道我是谁吗?",
        session_id=SESSION_B
    )

    if not result_2:
        print("TEST 2 FAILED: Could not complete invocation")
        return

    # Analyze result
    if "张三" in result_2["response_text"] or "ABC" in result_2["response_text"]:
        print("\n[TEST 2 RESULT] ❌ UNEXPECTED: Agent remembered info from Session A")
    else:
        print("\n[TEST 2 RESULT] ✓ EXPECTED: Agent does not remember (different session)")

    time.sleep(2)

    # Test 3: Session A again (within 15 minutes) - Should remember
    print("\n" + "="*60)
    print("TEST 3: Session A (within 15 min) - Test session memory (should remember)")
    print("="*60)
    result_3 = tester.invoke_with_session(
        prompt="你知道我是谁吗?",
        session_id=SESSION_A
    )

    if not result_3:
        print("TEST 3 FAILED: Could not complete invocation")
        return

    # Analyze result
    if "张三" in result_3["response_text"] or "ABC" in result_3["response_text"]:
        print("\n[TEST 3 RESULT] ✓ EXPECTED: Agent remembered from earlier in Session A")
    else:
        print("\n[TEST 3 RESULT] ❌ UNEXPECTED: Agent forgot info from same session")

    time.sleep(2)

    # Test 4: Stop Session A and restart
    print("\n" + "="*60)
    print("TEST 4: Stop Session A, then restart (within 15 min)")
    print("="*60)

    tester.stop_session(SESSION_A)
    time.sleep(15)  # Wait for session to be fully stopped

    result_4 = tester.invoke_with_session(
        prompt="你知道我是谁吗?",
        session_id=SESSION_A
    )

    if not result_4:
        print("TEST 4 FAILED: Could not complete invocation")
        return

    # Analyze result
    if "张三" in result_4["response_text"] or "ABC" in result_4["response_text"]:
        print("\n[TEST 4 RESULT] ✓ Session state persisted after stop")
    else:
        print("\n[TEST 4 RESULT] ❌ Session state was lost after stop (expected behavior)")

    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    print(f"Test 1 (Session A - Introduce): {'✓ Completed' if result_1 else '❌ Failed'}")
    print(f"Test 2 (Session B - Cross-session): {'✓ Completed' if result_2 else '❌ Failed'}")
    print(f"Test 3 (Session A - Same session): {'✓ Completed' if result_3 else '❌ Failed'}")
    print(f"Test 4 (Session A - After stop): {'✓ Completed' if result_4 else '❌ Failed'}")
    print("="*60)

    # Optional: Extended test with 15-minute wait
    print("\n[OPTIONAL] To test 15-minute session timeout:")
    print("  1. Note current time")
    print("  2. Wait 15+ minutes")
    print("  3. Run: python test_runtime.py --test-timeout")


def test_timeout_scenario(agent_id: str = None, region: str = None):
    """
    Test scenario for 15-minute timeout.
    This should be run manually after waiting 15+ minutes from initial session.
    """
    print("="*60)
    print("AgentCore Runtime - 15-Minute Timeout Test")
    print("="*60)

    tester = RuntimeTester(agent_id=agent_id, region=region)
    SESSION_A = tester.generate_session_id("timeout-test")

    print(f"Session ID: {SESSION_A}\n")

    # Step 1: Introduce identity
    print("\nSTEP 1: Introducing identity to Session A")
    result_1 = tester.invoke_with_session(
        prompt="你好! 我叫李四, 来自XYZ技术公司。",
        session_id=SESSION_A
    )

    # Step 2: Wait 15 minutes
    print("\nSTEP 2: Waiting 15 minutes to test session timeout...")
    tester.wait_minutes(15)

    # Step 3: Test if session remembers after 15 minutes
    print("\nSTEP 3: Testing if session persists after 15 minutes")
    result_2 = tester.invoke_with_session(
        prompt="你知道我是谁吗?",
        session_id=SESSION_A
    )

    # Analyze result
    if result_2:
        if "李四" in result_2["response_text"] or "XYZ" in result_2["response_text"]:
            print("\n[RESULT] ✓ Session persisted after 15 minutes")
        else:
            print("\n[RESULT] ❌ Session expired after 15 minutes")


if __name__ == "__main__":
    import sys

    # Parse command line arguments
    agent_id = None
    region = None
    test_timeout = False
    custom_test = False

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--agent-id":
            if i + 1 >= len(sys.argv):
                print("Error: --agent-id requires a value")
                sys.exit(1)
            agent_id = sys.argv[i + 1]
            i += 2
        elif arg == "--region":
            if i + 1 >= len(sys.argv):
                print("Error: --region requires a value")
                sys.exit(1)
            region = sys.argv[i + 1]
            i += 2
        elif arg == "--test-timeout":
            test_timeout = True
            i += 1
        elif arg == "--custom":
            custom_test = True
            i += 1
        else:
            print(f"Unknown argument: {arg}")
            print()
            print("Usage: python test_runtime.py [OPTIONS]")
            print()
            print("Options:")
            print("  --agent-id ID   Agent Runtime ID (required, or set AGENT_RUNTIME_ID env var)")
            print("  --region REGION AWS region (optional, defaults to us-east-1)")
            print("  --test-timeout  Run 15-minute timeout test (will wait 15 minutes)")
            print("  --custom        Run quick custom test")
            print()
            print("Examples:")
            print("  python test_runtime.py --agent-id conversation_agent_claudeagentsdk-xT5RdG23nX --region us-east-1")
            print("  export AGENT_RUNTIME_ID=conversation_agent_claudeagentsdk-xT5RdG23nX")
            print("  python test_runtime.py")
            sys.exit(1)

    # Run appropriate test scenario
    try:
        if test_timeout:
            test_timeout_scenario(agent_id=agent_id, region=region)
        elif custom_test:
            # Quick custom test
            tester = RuntimeTester(agent_id=agent_id, region=region)
            if tester.agent_id:
                SESSION_A = tester.generate_session_id("custom-test")
                print(f"\nSession ID: {SESSION_A}\n")
                tester.invoke_with_session("你好! 我叫王五, 来自DEF公司。", SESSION_A)
                tester.invoke_with_session("你知道我是谁吗?", SESSION_A)
        else:
            # Run standard test scenario
            run_test_scenario(agent_id=agent_id, region=region)
    except Exception as e:
        # Error already printed by RuntimeTester.__init__
        sys.exit(1)
