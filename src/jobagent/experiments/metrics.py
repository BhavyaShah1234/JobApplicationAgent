"""Count actual provider requests separately from logical planning operations."""
from langchain_core.callbacks import BaseCallbackHandler


class CallAudit(BaseCallbackHandler):
    def __init__(self):
        self.calls=0
        self.reported_tokens=None

    def on_chat_model_start(self,serialized,messages,**kwargs):
        self.calls+=1

    def on_llm_end(self,response,**kwargs):
        output=response.llm_output or {}
        usage=output.get('token_usage') or output.get('usage') or {}
        total=usage.get('total_tokens')
        if total is not None:self.reported_tokens=(self.reported_tokens or 0)+total
