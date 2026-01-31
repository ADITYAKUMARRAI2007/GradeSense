import asyncio

# Global semaphore to limit concurrent CPU-bound tasks (PDF conversion)
# preventing CPU starvation in the main event loop
conversion_semaphore = asyncio.Semaphore(1)

# Global semaphore to limit concurrent LLM API calls
# preventing rate limit bursts and ensuring stability
llm_semaphore = asyncio.Semaphore(1)
