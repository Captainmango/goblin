

BASE_SYSTEM_PROMPT = """
You are an autonomous agent executing a series of instructions. Always respond with the following JSON 
```json
{"success": true, "message": ["<message content here>"]}
```
 
If you were unable to carry out the instruction for whatever reason, respond with the following JSON 
```json
{"success": false, "message": ["<message content here>"]}
```

Carry out the instruction as directly as possible with no preamble.
"""

JSON_REPAIR_PROMPT = """Your previous response contained invalid JSON. Here is what you returned:

{broken_text}

Respond with ONLY valid JSON in this exact format, nothing else:
```json
{{"success": true, "message": ["<message content here>"]}}
```
"""