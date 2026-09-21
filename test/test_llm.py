import sys
import time
from pathlib import Path

from openai import OpenAI


LLM_BASE_URL = "https://4000--main--dev--sinhnq3.coder.vts-ai.space/v1"
LLM_API_KEY = "sk-UDJyzHZ9IsTSKcCBwXY30g"
LLM_MODEL_NAME = "google/gemma-4-26B-A4B-it"

client = OpenAI(
    base_url=LLM_BASE_URL,
    api_key=LLM_API_KEY,
)


def call_llm(prompt: str) -> str:
    response = client.chat.completions.create(
        model=LLM_MODEL_NAME,
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
        temperature=0.0,
    )

    return response.choices[0].message.content


if __name__ == "__main__":
    prompt = "Xin chào! Hãy giới thiệu ngắn gọn về bạn."
    
    result = call_llm(prompt)
    print(result)