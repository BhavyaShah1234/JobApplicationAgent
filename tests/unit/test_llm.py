from langchain_core.embeddings import Embeddings
from pydantic import BaseModel

from jobagent import llm
from jobagent.config import Settings


class _Broken(Embeddings):
    def embed_documents(self, texts):
        raise ConnectionError("voyager down")

    def embed_query(self, text):
        raise ConnectionError("voyager down")


class _Fixed(Embeddings):
    def embed_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.0]


def _settings() -> Settings:
    return Settings(openai_api_key="test", vault_key="")


def test_chat_has_ollama_fallback():
    runnable = llm.chat("fast", _settings())
    assert runnable.runnable.model_name == "qwen3-30b-a3b"
    assert [f.model for f in runnable.fallbacks] == ["qwen3:4b"]


def test_structured_binds_schema_on_both_models():
    class Answer(BaseModel):
        word: str

    runnable = llm.structured(Answer, "planner", _settings())
    assert len(runnable.fallbacks) == 1


def test_embeddings_fall_back_and_flag_it():
    emb = llm.FallbackEmbeddings(_Broken(), _Fixed())
    assert emb.embed_query("x") == [1.0, 0.0]
    assert emb.used_fallback
    assert emb.embed_documents(["a", "b"]) == [[1.0, 0.0], [1.0, 0.0]]


def test_embeddings_primary_used_when_healthy():
    emb = llm.FallbackEmbeddings(_Fixed(), _Broken())
    emb.embed_query("x")
    assert not emb.used_fallback
