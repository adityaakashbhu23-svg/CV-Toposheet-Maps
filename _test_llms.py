import os, sys
from dotenv import load_dotenv
load_dotenv(r'C:\CV- Toposheet\.env')
sys.path.insert(0, r'C:\CV- Toposheet')

PROMPT = [{'role': 'user', 'content': 'Reply with just the word OK'}]
results = {}

# 1. OpenAI
try:
    from openai import OpenAI
    r = OpenAI(api_key=os.getenv('OPENAI_API_KEY')).chat.completions.create(
        model='gpt-4o-mini', messages=PROMPT, max_tokens=5)
    results['OpenAI (gpt-4o-mini)'] = 'OK - ' + r.choices[0].message.content.strip()
except Exception as e:
    results['OpenAI (gpt-4o-mini)'] = 'FAIL - ' + str(e)[:80]

# 2. Gemini key 1
try:
    from google import genai
    from google.genai import types as gt
    c = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
    r = c.models.generate_content(model='gemini-2.5-flash', contents='Reply with just the word OK',
        config=gt.GenerateContentConfig(max_output_tokens=10))
    results['Gemini key1 (gemini-2.5-flash)'] = 'OK - ' + r.text.strip()
except Exception as e:
    results['Gemini key1 (gemini-2.5-flash)'] = 'FAIL - ' + str(e)[:80]

# 3. Gemini key 2
try:
    from google import genai
    from google.genai import types as gt
    c2 = genai.Client(api_key=os.getenv('GEMINI_API_KEY_2'))
    r2 = c2.models.generate_content(model='gemini-2.5-flash', contents='Reply with just the word OK',
        config=gt.GenerateContentConfig(max_output_tokens=10))
    results['Gemini key2 (gemini-2.5-flash)'] = 'OK - ' + r2.text.strip()
except Exception as e:
    results['Gemini key2 (gemini-2.5-flash)'] = 'FAIL - ' + str(e)[:80]

# 4. Groq
try:
    from groq import Groq
    r = Groq(api_key=os.getenv('GROQ_API_KEY')).chat.completions.create(
        model='llama-3.3-70b-versatile', messages=PROMPT, max_tokens=5)
    results['Groq (llama-3.3-70b)'] = 'OK - ' + r.choices[0].message.content.strip()
except Exception as e:
    results['Groq (llama-3.3-70b)'] = 'FAIL - ' + str(e)[:80]

# 5. Claude
try:
    import anthropic
    r = anthropic.Anthropic(api_key=os.getenv('CLAUDE_API_KEY')).messages.create(
        model='claude-haiku-4-5', max_tokens=5, messages=PROMPT)
    results['Claude (claude-3-5-haiku)'] = 'OK - ' + r.content[0].text.strip()
except Exception as e:
    results['Claude (claude-3-5-haiku)'] = 'FAIL - ' + str(e)[:80]

# 6. Grok
try:
    from openai import OpenAI as OAI
    r = OAI(api_key=os.getenv('GROK_API_KEY'), base_url='https://api.x.ai/v1').chat.completions.create(
        model='grok-3-mini', messages=PROMPT, max_tokens=5)
    results['Grok (grok-3-mini)'] = 'OK - ' + r.choices[0].message.content.strip()
except Exception as e:
    results['Grok (grok-3-mini)'] = 'FAIL - ' + str(e)[:80]

# 7. Vertex AI
try:
    from google import genai as vai
    vc = vai.Client(vertexai=True, project=os.getenv('VERTEX_PROJECT'), location='us-central1')
    r = vc.models.generate_content(model='gemini-2.5-flash', contents='Reply with just the word OK')
    results['Vertex AI (gemini-2.5-flash)'] = 'OK - ' + r.text.strip()
except Exception as e:
    results['Vertex AI (gemini-2.5-flash)'] = 'FAIL - ' + str(e)[:80]

print('\n=== LLM Provider Test Results ===')
for name, res in results.items():
    icon = 'OK  ' if res.startswith('OK') else 'FAIL'
    print(f'  [{icon}] {name}: {res}')
