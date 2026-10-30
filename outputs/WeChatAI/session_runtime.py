"""Short database critical sections and bounded per-conversation FIFO workers."""
from __future__ import annotations

import logging
import queue
import threading
import time

LOG = logging.getLogger('wechat_ai_bridge')


class LockedDB:
    def __init__(self, db, lock):
        self._db, self._lock = db, lock

    def __getattr__(self, name):
        value = getattr(self._db, name)
        if not callable(value): return value
        def access(*args, **kwargs):
            with self._lock: return value(*args, **kwargs)
        return access


class LockedState(dict):
    """Protect dictionary mutations with the same lock used by JSON snapshots."""
    def __init__(self, value, lock):
        self.lock = lock
        super().__init__((k, self.wrap(v)) for k,v in value.items())

    def wrap(self, value):
        return LockedState(value, self.lock) if isinstance(value, dict) and not isinstance(value, LockedState) else value

    def __setitem__(self, key, value):
        with self.lock: super().__setitem__(key, self.wrap(value))

    def __delitem__(self, key):
        with self.lock: super().__delitem__(key)

    def setdefault(self, key, default=None):
        with self.lock:
            if key not in self: self[key] = default
            return self[key]

    def pop(self, key, *default):
        with self.lock: return super().pop(key, *default)

    def update(self, *args, **kwargs):
        with self.lock:
            for key,value in dict(*args, **kwargs).items(): self[key] = value

    def clear(self):
        with self.lock: super().clear()


class SessionDispatcher:
    def __init__(self, handler, max_pending=128):
        self.handler, self.max_pending = handler, max_pending
        self.queues, self.threads = {}, {}
        self.lock, self.closed = threading.Lock(), False

    def enqueue(self, peer, task):
        with self.lock:
            if self.closed: return False
            if peer not in self.queues:
                q = self.queues[peer] = queue.Queue(maxsize=self.max_pending)
                worker = self.threads[peer] = threading.Thread(target=self._run,args=(peer,q),daemon=True)
                worker.start()
            try: self.queues[peer].put_nowait(task)
            except queue.Full: return False
            return True

    def _run(self, peer, q):
        while True:
            task = q.get()
            try:
                if task is None: return
                self.handler(peer,task)
            except Exception:
                LOG.exception('conversation task failed; isolated peer=%s',peer)
            finally: q.task_done()

    def join(self, timeout=2):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if all(q.unfinished_tasks==0 for q in self.queues.values()): return True
            time.sleep(.01)
        return all(q.unfinished_tasks==0 for q in self.queues.values())

    def shutdown(self, timeout=2):
        with self.lock:
            if self.closed: return
            self.closed=True
            for q in self.queues.values():
                try: q.put_nowait(None)
                except queue.Full: pass
        deadline=time.monotonic()+timeout
        for worker in self.threads.values(): worker.join(max(0,deadline-time.monotonic()))


class SessionSlice:
    def __init__(self, db, peer, message): self.db,self.peer,self.message=db,peer,message
    def get_sessions(self,limit=1000): return [{'username':self.peer}]
    def get_new_messages(self,peer,since_seq,limit=100):
        if peer!=self.peer: return []
        if int(self.message['sort_seq'])>since_seq: return [self.message]
        # Followup lookahead is read-only; normal work remains one queued message.
        return self.db.get_new_messages(peer,since_seq=since_seq,limit=limit)
    def __getattr__(self,name): return getattr(self.db,name)


class InlineGroupDispatcher:
    def __init__(self,handler): self.handler=handler
    def enqueue(self,peer,task): self.handler(task)


class ConversationRuntime:
    def __init__(self,db,config,state,process,group_handler=None,on_observed=None,coalesce=None):
        self.db,self.config,self.state,self.process=db,config,state,process
        self.scanned={}
        self.inline=InlineGroupDispatcher(group_handler) if group_handler else None
        self.on_observed=on_observed
        self.coalesce=coalesce
        self.dispatcher=SessionDispatcher(self._process,int(config.get('session_queue_max_pending',128)))

    def _process(self,peer,msg):
        if '_ambient_task' in msg:
            if self.inline:self.inline.enqueue(peer,msg['_ambient_task'])
            return
        # A prior task may have consumed this as a same-sender sticker followup.
        if int(msg['sort_seq'])<=int(self.state['chats'].get(peer,{}).get('last_seq',0)): return
        # A burst of messages becomes one turn; the merged rows are then skipped by the check above.
        if self.coalesce: msg=self.coalesce(peer,msg)
        self.process(SessionSlice(self.db,peer,msg),self.config,self.state,group_dispatcher=self.inline)

    def poll(self):
        for session in self.db.get_sessions(limit=1000):
            peer=session['username']
            since=max(int(self.state['chats'].get(peer,{}).get('last_seq',self.state['activated_at']*1000-1)),self.scanned.get(peer,0))
            for msg in self.db.get_new_messages(peer,since_seq=since,limit=100):
                if not self.dispatcher.enqueue(peer,dict(msg)): break
                if self.on_observed: self.on_observed(peer,msg)
                self.scanned[peer]=max(self.scanned.get(peer,0),int(msg['sort_seq']))

    def shutdown(self): self.dispatcher.shutdown()
