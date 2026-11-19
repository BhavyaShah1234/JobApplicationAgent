"""Content-free writer callback metrics; missing token usage remains unknown."""
from langchain_core.callbacks import BaseCallbackHandler


class ModelAudit(BaseCallbackHandler):
    """Count actual writer starts and reported tokens without retaining prompts."""
    def __init__(self):
        self.starts=0
        self.tokens=None

    def on_chat_model_start(self,*args,**kwargs):
        """Count provider requests, including structured-output retries."""
        self.starts+=1

    def on_llm_end(self,response,**kwargs):
        """Prefer aggregate usage; use message usage only when it is absent."""
        output=response.llm_output or {}
        usage=output.get('token_usage') or output.get('usage') or {}
        total=usage.get('total_tokens')
        if total is None:
            values=[getattr(getattr(g,'message',None),'usage_metadata',None) for row in response.generations for g in row]
            reported=[v['total_tokens'] for v in values if v and v.get('total_tokens') is not None]
            total=sum(reported) if reported else None
        if isinstance(total,int) and not isinstance(total,bool) and total>=0:
            self.tokens=(self.tokens or 0)+total
