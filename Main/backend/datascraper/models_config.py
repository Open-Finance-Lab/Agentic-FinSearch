"""
Model configuration for FinGPT backend.
Central configuration for all supported LLM models.
"""

MODELS_CONFIG = {
    "FinGPT-Light": {
        "provider": "openai",
        "model_name": "gpt-5.1-chat-latest",
        "supports_mcp": True,
        "supports_advanced": True,
        "max_tokens": 128000,
        "streaming": True,
        "tracing": True,
        "description": "Fast and efficient light-weight model"
    },
    "FinGPT": {
        "provider": "google",
        "model_name": "gemini-3-flash-preview",
        "supports_mcp": True,
        "supports_advanced": True,
        "max_tokens": 1048576,
        "streaming": False,
        "tracing": False,
        "description": "State-of-the-art financial model"
    },
    "Buffet-Agent": {
        "provider": "buffet",
        "model_name": "Buffet-Agent",
        "endpoint_url": "https://l7d6yqg7nzbkumx8.us-east-1.aws.endpoints.huggingface.cloud",
        "supports_mcp": True,
        "supports_advanced": True,
        "max_tokens": 400000,
        "streaming": True,
        "tracing": True,
        "description": "The power of the Warren, in the palm of my hands"
    },


}

# Fallback when a client omits `models`; must be a MODELS_CONFIG key.
DEFAULT_MODEL = "FinGPT"

PROVIDER_CONFIGS = {
    "openai": {
        "base_url": None,
        "env_key": "OPENAI_API_KEY",
        "client_class": "OpenAI"
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "env_key": "DEEPSEEK_API_KEY",
        "client_class": "OpenAI"
    },
    "anthropic": {
        "base_url": None,
        "env_key": "ANTHROPIC_API_KEY",
        "client_class": "Anthropic"
    },
    "google": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "env_key": "GOOGLE_API_KEY",
        "client_class": "OpenAI"
    },
    "buffet": {
        "base_url": "https://l7d6yqg7nzbkumx8.us-east-1.aws.endpoints.huggingface.cloud",
        "env_key": "BUFFET_AGENT_API_KEY",
        "client_class": None
    }
}

def get_model_config(model_id: str) -> dict:
    """Get configuration for a specific model."""
    return MODELS_CONFIG.get(model_id, None)

def get_provider_config(provider: str) -> dict:
    """Get configuration for a specific provider."""
    return PROVIDER_CONFIGS.get(provider, None)

def get_available_models() -> list[str]:
    """Get list of all available model IDs."""
    return list(MODELS_CONFIG.keys())

def get_models_by_provider(provider: str) -> list[str]:
    """Get list of model IDs for a specific provider."""
    return [
        model_id 
        for model_id, config in MODELS_CONFIG.items() 
        if config["provider"] == provider
    ]

def validate_model_support(model_id: str, feature: str) -> bool:
    """Check if a model supports a specific feature (e.g., mcp, advanced).

    Accepts either a display name ('FinGPT') or a resolved model name
    ('gpt-5.2-chat-latest', 'gemini-3-flash-preview').
    """
    config = get_model_config(model_id)
    if not config:
        # Reverse lookup: model_id might be a resolved model_name
        for _id, cfg in MODELS_CONFIG.items():
            if cfg.get("model_name") == model_id:
                config = cfg
                break
    if not config:
        return False
    return config.get(f"supports_{feature}", False)

RESEARCH_CONFIG = {
    "planner_model": "gpt-5-mini",
    "research_model": "gpt-5.2-chat-latest",
    "max_iterations": 3,
    "max_sub_questions": 5,
    "parallel_searches": True,
}


def get_research_config() -> dict:
    """Return a copy of the research configuration (safe to mutate)."""
    return dict(RESEARCH_CONFIG)
