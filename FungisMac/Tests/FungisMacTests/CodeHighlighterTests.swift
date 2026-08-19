import Foundation
import SwiftUI
import Testing
@testable import FungisMac

private func colors(_ painted: AttributedString) -> [Color] {
    painted.runs.compactMap(\.foregroundColor)
}

@Test func aBlockCommentKeepsItsColorAcrossLines() {
    // 줄마다 따로 칠하면 여는 줄만 주석이 되고 가운데가 코드처럼 보인다.
    let painted = CodeHighlighter.paint(
        lines: ["/* 여는 줄", "가운데", "닫는 줄 */", "let x = 1"],
        path: "a.swift"
    )
    #expect(colors(painted[1]) == [CodeColors.comment])
    #expect(colors(painted[2]) == [CodeColors.comment])
    // 닫은 다음 줄은 다시 코드다.
    #expect(colors(painted[3]).contains(CodeColors.pink))
}

@Test func pythonUsesHashAndDoesNotTreatSlashesAsComments() {
    let painted = CodeHighlighter.paint(
        lines: ["# 주석이다", "path = a // b"], path: "x.py"
    )
    #expect(colors(painted[0]) == [CodeColors.comment])
    #expect(!colors(painted[1]).contains(CodeColors.comment))
}

@Test func stringsAndKeywordsAndNumbersAreToldApart() {
    let painted = CodeHighlighter.paint(
        lines: [#"if name == "hi" { return 42 }"#], path: "a.swift"
    )
    let seen = colors(painted[0])
    #expect(seen.contains(CodeColors.pink))    // if · return
    #expect(seen.contains(CodeColors.yellow))  // "hi"
    #expect(seen.contains(CodeColors.purple))  // 42
}

@Test func aCommentMarkerInsideAStringIsNotAComment() {
    // 문자열을 먼저 집지 않으면 URL 하나에 줄 절반이 주석이 된다.
    let painted = CodeHighlighter.paint(
        lines: [#"let url = "https://example.com" + tail"#], path: "a.swift"
    )
    #expect(!colors(painted[0]).contains(CodeColors.comment))
}
