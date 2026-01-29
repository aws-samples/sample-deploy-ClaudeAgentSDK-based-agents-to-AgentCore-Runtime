"""
Conversational Agent using Claude Agent SDK for AWS Bedrock AgentCore Runtime
"""

import sys
import logging

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock

app = BedrockAgentCoreApp()

SYSTEM_PROMPT = "You are a friendly and helpful assistant. Be concise in your responses."


async def invoke_claude(prompt: str) -> str:
    """Invoke Claude using Claude Agent SDK."""
    logger.info("Invoking Claude Agent SDK...")
    logger.debug(f"Prompt: {prompt[:100]}...")

    result = []

    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
        )
    ):
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
    """Main entry point for AWS Bedrock AgentCore Runtime."""
    logger.info("=" * 50)
    logger.info("Agent invocation started")
    logger.debug(f"Payload: {payload}")
    logger.debug(f"Context: {context}")

    try:
        user_prompt = payload.get("prompt", "Hello")
        logger.info(f"User prompt: {user_prompt}")

        response = await invoke_claude(user_prompt)

        logger.info(f"Response: {response[:200]}...")
        logger.info("Agent invocation completed successfully")
        return {"result": response}

    except Exception as e:
        logger.error(f"Error: {type(e).__name__}: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {"result": f"Error: {str(e)}"}


if __name__ == "__main__":
    logger.info("Starting BedrockAgentCoreApp...")
    app.run()
