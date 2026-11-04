import os
import sys

try:
    import anthropic
except ImportError:
    sys.exit("The package is missing. Run:  python -m pip install anthropic")

key = os.environ.get("ANTHROPIC_API_KEY")
if not key:
    sys.exit('No key found in this terminal. Run:  $env:ANTHROPIC_API_KEY = "your-key-here"')
print("Key found, starts with", key[:7] + "...")

model = os.environ.get("STUDYGENIE_MODEL", "claude-haiku-5-5")
try:
    reply = anthropic.Anthropic(api_key=key).messages.create(
        model=model, max_tokens=20, messages=[{"role": "user", "content": "Say hi in three words."}])
    print(f"WORKS   {model}: {reply.content[0].text.strip()}")
except Exception as err:
    print(f"FAILED  {model}: {type(err).__name__} {getattr(err, 'status_code', '')} {str(err)[:200]}")