import Testing
@testable import FungisMac

@Test func insertsLineBreaksBeforeStructureMarkers() {
    let source = "요약 ■첫 항목 ①세부 내용 ✓완료"

    #expect(MessagePrettyPrinter.format(source) == "요약\n■첫 항목\n①세부 내용\n✓완료")
}

@Test func keepsMarkersThatSitInsideASentence() {
    // 목록 머리표가 아니라 본문 안에 쓴 기호다. 쪼개면 뜻이 망가진다.
    let source = "③①② 전부 태운다 — 네 권고 순서(③→①→②) 그대로."

    #expect(MessagePrettyPrinter.format(source) == source)
}

@Test func stillSplitsMarkersThatFollowASpace() {
    let source = "요약 ■ 첫 항목 ■ 둘째 항목"

    #expect(MessagePrettyPrinter.format(source) == "요약\n■ 첫 항목\n■ 둘째 항목")
}

@Test func preservesExistingLinesAndLeadingMarkers() {
    let source = "■첫 줄\n②둘째 줄\n일반 문장"

    #expect(MessagePrettyPrinter.format(source) == source)
}

@Test func escapedNewlinesBecomeLayoutLinesOnlyInPretty() {
    let source = "요약\\n\\n1) 첫째\\n2) 둘째"

    #expect(MessagePrettyPrinter.layoutText(source) == "요약\n\n1) 첫째\n2) 둘째")
    #expect(source == "요약\\n\\n1) 첫째\\n2) 둘째")
}

@Test func inlineParenthesisNumbersBecomePrettyLines() {
    let source = "정리 1) 첫째 2) 둘째"

    #expect(MessagePrettyPrinter.layoutText(source) == "정리\n1) 첫째\n2) 둘째")
    #expect(source == "정리 1) 첫째 2) 둘째")
}

@Test func parenthesisReferencesAndCodeAreNotNumberedLists() {
    #expect(MessagePrettyPrinter.layoutText("함수(1) 결과") == "함수(1) 결과")
    let fenced = "```text\necho 1) 그대로\n```"
    #expect(MessagePrettyPrinter.layoutText(fenced) == fenced)
}

@Test func escapedNewlinesInsideCodeFencesStayLiteral() {
    let source = #"앞\n```text"# + "\n" + #"a\nb"# + "\n" + #"```\n뒤"#
    let layout = MessagePrettyPrinter.layoutText(source)

    #expect(layout.hasPrefix("앞\n```text\n"))
    #expect(layout.contains("a\\nb"))
    #expect(layout.hasSuffix("```\n뒤"))
    #expect(MessagePrettyPrinter.layoutText(#"C:\\new"#) == #"C:\\new"#)
}

@Test func rawSourceRemainsAvailableWithoutFormatting() {
    let source = "한 줄 ■원문"

    #expect(source == "한 줄 ■원문")
    #expect(MessagePrettyPrinter.format(source) != source)
}

@Test func convertsDoubleAsterisksToStableHighlighterRuns() {
    let source = "결론 **승인 필요** 다음 **주의**"
    let first = MessagePrettyPrinter.prettyText(source, seed: 42)
    let second = MessagePrettyPrinter.prettyText(source, seed: 42)

    #expect(String(first.characters) == "결론 승인 필요 다음 주의")
    #expect(first == second)
    #expect(first.runs.contains { $0.backgroundColor != nil })
}

@Test func preservesAnUnclosedHighlightMarker() {
    let source = "아직 **미완성"

    #expect(String(MessagePrettyPrinter.prettyText(source, seed: 1).characters) == source)
}
