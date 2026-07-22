# LLM Configuration

Goblin supports two ways to specify an LLM: the `model` shorthand and the full `llm` configuration dict.

## `model` Shorthand

A simple string identifier:

```yaml
defaults:
  model: anthropic/claude-sonnet-4-6
```

This is passed directly to the OpenHands SDK, which resolves authentication from environment variables.

## Full `llm` Dict

For fine-grained control over authentication, retries, temperature, and more:

```yaml
defaults:
  llm:
    model: anthropic/claude-sonnet-4-6
    api_key: $GOBLIN_MODEL_API_KEY
    base_url: https://llm-proxy.example.com
    temperature: 0.7
    max_output_tokens: 4096
    num_retries: 3
```

## Precedence

The effective configuration is resolved in this order:

1. Phase `llm`
2. Phase `model`
3. Blueprint `defaults.llm`
4. Blueprint `defaults.model`
5. None (OpenHands SDK falls back to environment-based credentials)

## Authentication Modes

### API Key

```yaml
llm:
  model: openai/gpt-4o
  auth_type: api_key
  api_key: $OPENAI_API_KEY
```

### Subscription

```yaml
llm:
  model: openai/gpt-4o
  auth_type: subscription
  subscription_vendor: openai
```

### AWS Bedrock

```yaml
llm:
  model: bedrock/anthropic.claude-3-sonnet
  aws_access_key_id: $AWS_ACCESS_KEY_ID
  aws_secret_access_key: $AWS_SECRET_ACCESS_KEY
  aws_region_name: us-east-1
```

## Fallback Strategy

Configure alternate LLMs to try on transient failure:

```yaml
llm:
  model: anthropic/claude-sonnet-4-6
  fallback_strategy:
    fallback_llms:
      - anthropic/claude-opus-4
      - openai/gpt-4o
```

## Environment Variable Resolution

String values in the `llm` dict support `$VAR` environment variable references and `{variable}` context interpolation. Dict configs have environment variables resolved before being passed to the OpenHands SDK.
