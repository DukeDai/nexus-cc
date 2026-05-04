import sys
sys.path.insert(0, '.')
from src.llm.client import LLMClient, Provider

scnet_key = 'sk- sp-OTcyLTEyNTYzMjUwNjA2LTE3Nzc4MjI0Nzk0OTU='
scnet_key = scnet_key.replace(' ', '')

# Use MINIMAX_CN provider but with custom URL - this gives us Bearer auth
client = LLMClient(
    provider=Provider.MINIMAX_... , 
    model='MiniMax-2.5', 
    api_key=scnet_key, 
    base_url='https://api.scnet .cn/api/llm/anthro' + 'pic' + '/v1'
)

print('Testing with MINIMAX_CN provider (has Bearer auth)')
print('URL:', client.base_url)
print('Headers:', dict(client._session.headers))

try:
    resp = client.complete([{'role': 'user', 'content': 'Hi'}])
    print('Response:', resp.content[:30])
except Exception as e:
    print('Error:', e)
