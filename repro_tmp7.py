import time
from chat_service import run_chat_turn

class EmptyStream:
    def __iter__(self):
        return iter(())

class FakeClient:
    def __init__(self):
        self.chat = type("C", (), {"completions": type("O", (), {
            "create": lambda self, **kw: EmptyStream(),
        })()})()

start = time.time()
gen = run_chat_turn(client=FakeClient(), model_id="m",
          messages=[{"role":"user","content":"hi"}],
          tools=None, max_iterations=3)
count = 0
for ev in gen:
    count += 1
    print("ev", ev["type"], round(time.time()-start,2))
    if count >= 5:
        break
print("done after", round(time.time()-start,2))
