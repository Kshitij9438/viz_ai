import os
from openai import OpenAI

# Set your GitHub token via environment variable
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

client = OpenAI(
    base_url="https://models.inference.ai.azure.com",
    api_key=GITHUB_TOKEN
)

response = client.chat.completions.create(
    model="gpt-4.1-mini",
    messages=[
        {"role": "user", "content": "Tell me a short programming joke."}
    ],
    temperature=0.7
)

print(response.choices[0].message.content)