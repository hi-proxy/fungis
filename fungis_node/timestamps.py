"""노드가 남기는 시각은 한 모양이다.

같은 식이 다섯 군데에 흩어져 있었다. 하나가 바뀌면 나머지와 어긋나는데,
어긋난 시각은 비교할 때까지 아무 소리도 안 낸다.
"""

from __future__ import annotations

from datetime import datetime, timezone


def stamp(moment: datetime | None = None) -> str:
    """UTC 밀리초까지. DB 의 `strftime('%Y-%m-%dT%H:%M:%fZ')` 와 같은 모양이다."""
    moment = moment or datetime.now(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
