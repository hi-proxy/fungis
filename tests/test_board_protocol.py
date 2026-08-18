"""보드를 한 번 읽고 파악되는가.

JSON을 줄 때는 한 줄에 uuid가 아홉 번 나왔고 그중 셋이 같은 제목을 가리켰다.
에이전트는 그것을 눈으로 맞춰야 했고 그래서 보드를 두 번 읽었다.
"""

from fungis_node.agent_cli import (
    render_board,
    resolve_ticket,
    ticket_line,
    ticket_names,
)

BOARD = [
    {
        "project_id": "p-arch",
        "project_name": "ARCHIVIA bookclub",
        "ticket_prefix": "ARCH",
        "nodes": [
            {"id": "a11", "number": 11, "title": "표지 정리", "status": "active",
             "state": "active", "blocked_by": [], "blocks": []},
            {"id": "a12", "number": 12, "title": "선행 있는 일", "status": "todo",
             "state": "waiting", "blocked_by": ["m31"], "blocks": ["f11"]},
        ],
    },
    {
        "project_id": "p-fung",
        "project_name": "fungis",
        "ticket_prefix": "FUNG",
        "nodes": [
            {"id": "f10", "number": 10, "title": "끝난 일", "status": "done",
             "state": "done", "blocked_by": [], "blocks": []},
            {"id": "f11", "number": 11, "title": "기다리는 일", "status": "todo",
             "state": "waiting", "blocked_by": ["a12"], "blocks": []},
        ],
    },
    {
        "project_id": "p-mei",
        "project_name": "mei",
        "ticket_prefix": "MEI",
        "nodes": [
            {"id": "m31", "number": 31, "title": "맨 앞의 일", "status": "active",
             "state": "active", "blocked_by": [], "blocks": ["a12"]},
        ],
    },
]


def test_one_read_shows_the_whole_chain_in_both_directions():
    """mei가 archivia를 막고 archivia가 fungis를 막는다. 세 줄에 다 있어야 한다."""
    text = render_board(BOARD, you="p-fung", role="@dispatch.dev")
    lines = {line.split("  ")[0]: line for line in text.split("\n") if "-" in line[:8]}

    assert "blocks ARCH-12" in lines["MEI-31"], "선행이 자기가 막는 것을 알아야 한다"
    assert "blockedBy MEI-31" in lines["ARCH-12"]
    assert "blocks FUNG-11" in lines["ARCH-12"]
    assert "blockedBy ARCH-12" in lines["FUNG-11"]
    # 내 방이 어디인지 한 줄로 말한다. 이게 없어서 매번 init을 다시 불렀다.
    assert "you       FUNG @dispatch.dev" in text


def test_the_read_stays_shorter_than_the_json_it_replaced():
    text = render_board(BOARD, you="p-fung")
    assert text.count("p-arch") == 1, "방 id는 rooms에서 한 번만 나온다"
    assert len(text) < 600


def test_a_title_with_spaces_or_quotes_cannot_break_a_line():
    board = [
        {
            "project_id": "p", "project_name": "room", "ticket_prefix": "R",
            "nodes": [
                {"id": "x", "number": 1, "status": "todo", "state": "todo",
                 "title": '두 줄\n짜리 "인용" 제목', "blocked_by": [], "blocks": []},
            ],
        }
    ]
    line = render_board(board).split("\n")[-1]
    # 줄바꿈이 티켓 경계다. 제목이 그것을 깨면 프로토콜이 무너진다.
    assert line.count("\n") == 0
    assert line.startswith("R-1  todo  ")
    assert '\\"인용\\"' in line


def test_a_ticket_can_be_named_by_number_only_inside_its_own_room():
    assert resolve_ticket(BOARD, "ARCH-12", "p-fung") == "a12"
    assert resolve_ticket(BOARD, "arch-12", "p-fung") == "a12", "대소문자는 안 따진다"
    # 맨 숫자는 내 방이다.
    assert resolve_ticket(BOARD, "11", "p-fung") == "f11"
    assert resolve_ticket(BOARD, "31", "p-mei") == "m31"


def test_an_unknown_name_is_refused_with_the_candidates():
    """조용히 아무거나 고르면 엉뚱한 방의 일이 바뀐다."""
    try:
        resolve_ticket(BOARD, "ARCH-99", "p-fung")
    except RuntimeError as error:
        assert "ARCH-11" in str(error) and "MEI-31" in str(error)
    else:
        raise AssertionError("없는 이름은 거절해야 한다")


def test_a_command_echoes_only_the_line_it_changed():
    """바뀐 줄만 돌려준다. 보드를 다시 읽게 하지 않는다."""
    line = ticket_line(BOARD, "m31")
    assert line.startswith("MEI-31  active")
    assert "\n" not in line


def test_every_ticket_name_says_its_room():
    names = set(ticket_names(BOARD).values())
    assert names == {"ARCH-11", "ARCH-12", "FUNG-10", "FUNG-11", "MEI-31"}
