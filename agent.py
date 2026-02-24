"""
Conversational Agent using Claude Agent SDK for AWS Bedrock AgentCore Runtime
"""

import sys
import logging
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, AssistantMessage, TextBlock

app = BedrockAgentCoreApp()

SYSTEM_PROMPT = "You are a friendly and helpful assistant. Be concise in your responses."

# Create a single ClaudeSDKClient instance for stateful conversations
client = ClaudeSDKClient(
    options=ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
    )
)


async def invoke_claude(prompt: str, session_id: str = "default") -> str:
    """Invoke Claude using Claude Agent SDK with session management."""
    logger.info(f"Invoking Claude Agent SDK with session: {session_id}")
    logger.debug(f"Prompt: {prompt[:100]}...")

    # Ensure client is connected
    try:
        server_info = await client.get_server_info()
        logger.debug(f"Server info: {server_info}")
        if not server_info:
            logger.info("Connecting to Claude Agent SDK server...")
            await client.connect()
            logger.info("Connected successfully")
    except Exception as e:
        logger.info(f"Connection check failed ({e}), attempting to connect...")
        await client.connect()
        logger.info("Connected successfully")

    result = []

    # Send query with session_id to maintain conversation history
    await client.query(prompt=prompt, session_id=session_id)

    # Receive response
    async for message in client.receive_response():
        logger.debug(f"Received message type: {type(message).__name__}")
        if isinstance(message, AssistantMessage):
            for block in message.content:
                logger.debug(f"Block type: {type(block).__name__}")
                if isinstance(block, TextBlock):
                    logger.debug(f"Text: {block.text[:100]}...")
                    result.append(block.text)

    final_result = "".join(result)
    logger.info(f"Response length: {len(final_result)}")
    return final_result


@app.entrypoint
async def agent_invocation(payload, context):
    """Main entry point for AWS Bedrock AgentCore Runtime.
    Invoked via POST /invocations (handled by BedrockAgentCoreApp).
    """
    request_timestamp = datetime.now(timezone.utc).isoformat()
    logger.info("=" * 50)
    logger.info(f"Request received at: {request_timestamp}")
    logger.info("Agent invocation started")
    logger.debug(f"Payload: {payload}")
    logger.debug(f"Context: {context}")

    try:
        # Extract session ID from context (provided by AgentCore Runtime)
        # Context is a RequestContext object, access attributes directly
        session_id = getattr(context, "session_id", "default") if context else "default"
        logger.info(f"Session ID: {session_id}")

        # AWS AgentCore format: payload contains "input" with "prompt"
        input_data = payload.get("input", {})
        user_prompt = input_data.get("prompt", payload.get("prompt", "Hello"))
        logger.info(f"User prompt: {user_prompt}")

        response_text = await invoke_claude(user_prompt, session_id=session_id)

        # Return AWS AgentCore format with timestamps for startup latency measurement
        response = {
            "message": {
                "role": "assistant",
                "content": [{"text": response_text}]
            },
            "request_timestamp": request_timestamp,
            "response_timestamp": datetime.now(timezone.utc).isoformat()
        }

        logger.info(f"Response: {response_text[:200]}...")
        logger.info("Agent invocation completed successfully")
        return {"output": response}

    except Exception as e:
        logger.error(f"Error: {type(e).__name__}: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {"output": {"error": str(e), "request_timestamp": request_timestamp}}


if __name__ == "__main__":
    logger.info("Starting BedrockAgentCoreApp...")
    app.run()
