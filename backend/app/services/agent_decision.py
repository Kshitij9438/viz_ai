import json
import re
from app.services.ollama_client import ollama

def safe_json_parse(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
    return {"intent": "chat", "confidence": 0.0, "reasoning": "parsing failed"}

async def propose_action(message: str, context: dict) -> dict:
    """
    Returns:
    {
        "intent": "chat" | "explore" | "refine" | "confirm" | "generate",
        "confidence": float (0–1),
        "reasoning": str
    }
    """
    
    prompt = f"""
Classify the user's intent.

Return JSON ONLY:
{{
  "intent": one of ["chat","explore","refine","confirm","generate"],
  "confidence": number between 0 and 1,
  "reasoning": short explanation
}}

User message:
{message}

Context:
{context}
"""

    try:
        raw = await ollama.complete(prompt)
        result = safe_json_parse(raw)
        
        # Ensure correct intent type
        if result.get("intent") not in ["chat", "explore", "refine", "confirm", "generate"]:
            result["intent"] = "chat"
            result["confidence"] = 0.0
            
        return result
    except Exception as e:
        return {"intent": "chat", "confidence": 0.0, "reasoning": str(e)}
