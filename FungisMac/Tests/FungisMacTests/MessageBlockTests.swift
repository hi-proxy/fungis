import Foundation
import Testing
@testable import FungisMac

@Test func aTableBecomesATableWithItsAlignments() {
    let blocks = MessageBlockParser.blocks(of: """
    앞말

    | 왼쪽 | 가운데 | 오른쪽 |
    |:-----|:------:|-------:|
    | a | b | c |
    | d | e | f |

    뒷말
    """)
    #expect(blocks.count == 3)
    guard case let .table(header, alignments, rows) = blocks[1] else {
        Issue.record("표로 안 읽혔다: \(blocks)")
        return
    }
    #expect(header == ["왼쪽", "가운데", "오른쪽"])
    #expect(alignments == [.leading, .center, .trailing])
    #expect(rows == [["a", "b", "c"], ["d", "e", "f"]])
}

@Test func aPipeInASentenceIsNotATable() {
    // 구분선이 없으면 그냥 파이프를 쓴 문장이다. 표로 만들면 멀쩡한 문장이
    // 칸으로 쪼개진다.
    let blocks = MessageBlockParser.blocks(of: "a | b 로 넘긴다")
    #expect(blocks.count == 1)
    if case .table = blocks[0] { Issue.record("문장이 표가 됐다") }
}

@Test func aShortRowStillJoinsTheTable() {
    // 한 칸 빠뜨렸다고 나머지 표를 통째로 버리면 손해가 더 크다.
    let blocks = MessageBlockParser.blocks(of: """
    | a | b | c |
    |---|---|---|
    | 1 | 2 |
    """)
    guard case let .table(_, _, rows) = blocks[0] else {
        Issue.record("표로 안 읽혔다")
        return
    }
    #expect(rows == [["1", "2", ""]])
}

@Test func aCodeFenceKeepsItsSourceExactly() {
    // 코드가 바뀌면 코드가 아니다. 안에서는 표도 머리표도 읽지 않는다.
    let source = """
    보라

    ```
    | 이건 | 표가 |
    |---|---|
      ■ 들여쓴 머리표
    ```
    """
    let blocks = MessageBlockParser.blocks(of: source)
    guard case let .code(body) = blocks[1] else {
        Issue.record("코드블록으로 안 읽혔다: \(blocks)")
        return
    }
    #expect(body == "| 이건 | 표가 |\n|---|---|\n  ■ 들여쓴 머리표")
}

@Test func anUnclosedFenceStillRendersAsCode() {
    // 열다 만 것을 문단으로 되돌리면 그 안의 표·머리표가 다시 읽혀서 더 망가진다.
    let blocks = MessageBlockParser.blocks(of: "```\nfungis inbox\n계속")
    #expect(blocks.count == 1)
    guard case let .code(body) = blocks[0] else {
        Issue.record("코드블록으로 안 읽혔다")
        return
    }
    #expect(body == "fungis inbox\n계속")
}

@Test func listsKeepTheirMarkersAndDepth() {
    let blocks = MessageBlockParser.blocks(of: """
    - 하나
      - 안쪽
    - 둘
    """)
    guard case let .bullets(items) = blocks[0] else {
        Issue.record("목록으로 안 읽혔다: \(blocks)")
        return
    }
    #expect(items.map(\.text) == ["하나", "안쪽", "둘"])
    #expect(items.map(\.depth) == [0, 1, 0])
    #expect(items.allSatisfy { $0.marker == nil })

    guard case let .bullets(numbered) = MessageBlockParser.blocks(of: """
    1. 먼저
    2) 다음
    """)[0] else {
        Issue.record("번호 목록으로 안 읽혔다")
        return
    }
    #expect(numbered.map(\.marker) == ["1.", "2."])
}

@Test func escapedNewlinesExposeParenthesisNumberedLists() {
    let blocks = MessageBlockParser.blocks(of: "요약\\n\\n1) 첫째\\n2) 둘째")
    #expect(blocks.count == 2)
    guard case let .bullets(items) = blocks[1] else {
        Issue.record("escaped newline 뒤 번호 목록을 읽지 못했다: \(blocks)")
        return
    }
    #expect(items.map(\.marker) == ["1.", "2."])
    #expect(items.map(\.text) == ["첫째", "둘째"])
}

@Test func inlineParenthesisNumbersExposeANumberedList() {
    let blocks = MessageBlockParser.blocks(of: "정리 1) 첫째 2) 둘째")
    #expect(blocks.count == 2)
    guard case let .bullets(items) = blocks[1] else {
        Issue.record("한 줄 번호 목록을 읽지 못했다: \(blocks)")
        return
    }
    #expect(items.map(\.marker) == ["1.", "2."])
    #expect(items.map(\.text) == ["첫째", "둘째"])
}

@Test func headingsNeedASpaceSoTagsSurvive() {
    guard case let .heading(level, text) =
            MessageBlockParser.blocks(of: "## 제목")[0] else {
        Issue.record("헤딩으로 안 읽혔다")
        return
    }
    #expect(level == 2 && text == "제목")

    // `#397` 은 방 번호지 헤딩이 아니다.
    let tag = MessageBlockParser.blocks(of: "#397 을 봐라")[0]
    if case .heading = tag { Issue.record("방 번호가 헤딩이 됐다") }
}

@Test func quotesAndRulesAreTheirOwnBlocks() {
    let blocks = MessageBlockParser.blocks(of: """
    > 인용 한 줄
    > 이어지는 줄

    ---

    끝말
    """)
    #expect(blocks.count == 3)
    guard case let .quote(text) = blocks[0] else {
        Issue.record("인용으로 안 읽혔다: \(blocks)")
        return
    }
    #expect(text == "인용 한 줄\n이어지는 줄")
    #expect(blocks[1] == .rule)
}

@Test func plainProseStaysOneParagraph() {
    // 못 읽은 줄은 문단으로 흘려보낸다. 모르는 문법을 만나도 글자는 남는다.
    let body = "그냥 두 줄짜리\n평범한 말이다"
    #expect(MessageBlockParser.blocks(of: body) == [.paragraph(body)])
}

@Test func aLeadingRoleLabelIsFoundButALinkIsNot() {
    #expect(MessagePrettyPrinter.leadingLabel(of: "[lead] 오늘 정리했다") == "[lead]")
    #expect(MessagePrettyPrinter.leadingLabel(of: "[FUNG-16] 끝") == "[FUNG-16]")

    // `[글](주소)` 는 링크다. 앞머리를 라벨로 잡으면 링크가 흐려진다.
    #expect(MessagePrettyPrinter.leadingLabel(of: "[문서](https://x) 를 봐라") == nil)
    // 맨 앞이 아니면 이름이 아니다.
    #expect(MessagePrettyPrinter.leadingLabel(of: "앞말 [lead] 뒷말") == nil)
    // 줄을 넘긴 대괄호도, 긴 것도 아니다.
    #expect(MessagePrettyPrinter.leadingLabel(of: "[열고\n닫는다]") == nil)
    #expect(MessagePrettyPrinter.leadingLabel(of: "[" + String(repeating: "가", count: 30) + "]") == nil)
    #expect(MessagePrettyPrinter.leadingLabel(of: "[닫는 것이 없다") == nil)
}

@Test func inlineCodeIsToldApartFromTheRest() {
    // 문단이 타는 길은 prettyText 다. inlineText 만 재면 문단에서 백틱이
    // 그대로 나오는 것을 놓친다 — 실제로 한 번 놓쳤다.
    let inParagraph = MessagePrettyPrinter.prettyText("`fungis inbox` 를 쳐라", seed: 1)
    #expect(String(inParagraph.characters) == "fungis inbox 를 쳐라")

    let painted = MessagePrettyPrinter.inlineText("`fungis inbox` 를 쳐라", seed: 1)
    #expect(String(painted.characters) == "fungis inbox 를 쳐라")

    // 닫는 백틱이 없으면 글자 그대로 남는다.
    let open = MessagePrettyPrinter.inlineText("`열다 말았다", seed: 1)
    #expect(String(open.characters) == "`열다 말았다")

    // 빈 백틱 쌍을 지우면 원문에 없던 일이 된다.
    let empty = MessagePrettyPrinter.inlineText("`` 두 개", seed: 1)
    #expect(String(empty.characters) == "`` 두 개")
}
