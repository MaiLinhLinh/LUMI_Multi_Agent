"""Test Gemini native API setup."""

from rag_manager.config import load_settings
from rag_manager.llm.gemini_client import GeminiClient

print("Settings loaded:")
settings = load_settings()
print(f"  Model: {settings.gemini_model}")
print(f"  Timeout: {settings.request_timeout_seconds}s")

print("\nCreating GeminiClient...")
client = GeminiClient(settings)
print("✅ GeminiClient created successfully!")
