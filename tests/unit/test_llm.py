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


def test_provider_construction_is_serial_but_requests_remain_parallel():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier,Lock
    from types import SimpleNamespace
    import time
    start=Barrier(6);guard=Lock();active=0;peak=0
    requests=Barrier(2)
    def factory():
        nonlocal active,peak
        with guard:active+=1;peak=max(peak,active)
        time.sleep(.01)
        with guard:active-=1
        return SimpleNamespace(invoke=lambda:requests.wait(timeout=2))
    def construct(_):
        start.wait(timeout=2)
        return llm.build_openai_client(factory)
    with ThreadPoolExecutor(6) as pool:clients=list(pool.map(construct,range(6)))
    assert peak==1
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(lambda client:client.invoke(),clients[:2]))==[0,1]


def test_all_provider_entry_points_share_the_initialization_guard(monkeypatch):
    seen=[]
    def construct(factory,**kwargs):
        seen.append(factory);return _Fixed()
    monkeypatch.setattr(llm,'build_openai_client',construct)
    llm.writer(settings=_settings())
    llm._primary('fast',_settings())
    llm.embeddings(_settings())
    assert seen==[llm.ChatOpenAI,llm.ChatOpenAI,llm.OpenAIEmbeddings]


def test_cold_parallel_chat_and_embedding_initialization_has_no_import_deadlock():
    import subprocess,sys,textwrap
    script=textwrap.dedent('''
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from jobagent import llm
        from jobagent.config import Settings
        settings=Settings(openai_api_key='test',vault_key='')
        ready=Barrier(8)
        def construct(i):
            ready.wait(timeout=10)
            return type(llm.writer(settings=settings) if i%2 else llm.embeddings(settings)).__name__
        with ThreadPoolExecutor(8) as pool:
            clients=list(pool.map(construct,range(8)))
        assert clients.count('ChatOpenAI')==clients.count('FallbackEmbeddings')==4
        print('8 clients initialized without network calls')
    ''')
    result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stdout+result.stderr
