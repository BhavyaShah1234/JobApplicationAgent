"""Shared filling-boundary metrics. Human waiting is excluded from active time."""
import time
from langchain_core.callbacks import BaseCallbackHandler


class FillingMetrics(BaseCallbackHandler):
    def __init__(self):
        self.opened=time.monotonic();self.started=None;self.stopped=None
        self.phase='setup';self.events=[];self.writer_starts=0;self.writer_tokens=None

    def begin(self):
        self.started=time.monotonic();self.phase='filling'

    def finish(self):
        if self.stopped is None:self.stopped=time.monotonic()
        self.phase='review'

    def on_chat_model_start(self,*args,**kwargs):
        if self.phase=='filling':self.writer_starts+=1

    def on_llm_end(self,response,**kwargs):
        if self.phase!='filling':return
        output=response.llm_output or {};usage=output.get('token_usage') or output.get('usage') or {}
        if usage.get('total_tokens') is not None:self.writer_tokens=(self.writer_tokens or 0)+usage['total_tokens']

    def snapshot(self):
        stop=self.stopped or time.monotonic();wait=sum(x['wait_s'] for x in self.events if x['phase']=='filling')
        elapsed=max(0,stop-self.started) if self.started is not None else 0
        return {'setup_s':(self.started or stop)-self.opened,'elapsed_filling_s':elapsed,
                'human_wait_s':wait,'active_filling_s':max(0,elapsed-wait),
                'human_prompt_events':sum(x['phase']=='filling' for x in self.events),
                'writer_model_starts':self.writer_starts,'writer_tokens_reported':self.writer_tokens,
                'events':list(self.events),'classification':'unreviewed; writer audit excludes embedding and SDK retries'}


class RecordedHitl:
    def __init__(self,wrapped,metrics):self.wrapped=wrapped;self.metrics=metrics
    def __getattr__(self,name):return getattr(self.wrapped,name)
    def ask(self,prompt,**kwargs):
        started=time.monotonic();phase=self.metrics.phase
        try:return self.wrapped.ask(prompt,**kwargs)
        finally:self.metrics.events.append({'phase':phase,'prompt':prompt,'wait_s':time.monotonic()-started,'classification':'unreviewed'})
