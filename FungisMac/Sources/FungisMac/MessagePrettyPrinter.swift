import SwiftUI

enum MessagePrettyPrinter {
    private static let lineBreakMarkers = Set(
        "■□●○◆◇▪▫①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳✓✔✅☑✗✕❌"
    )

    static func format(_ source: String) -> String {
        let normalized = layoutText(source)
        var result = ""
        var isAtLineStart = true

        for character in normalized {
            // 목록 머리표일 때만 줄을 나눈다. 앞이 공백이 아니면 본문 안에
            // 쓰인 기호다. ③①②처럼 잇달아 적거나 (③→①→②)처럼 괄호 안에
            // 넣은 것을 쪼개면 원문이 뜻하는 바가 망가진다.
            let followsSpace = result.last == " " || result.last == "\t"
            if lineBreakMarkers.contains(character), !isAtLineStart, followsSpace {
                while result.last == " " || result.last == "\t" {
                    result.removeLast()
                }
                if result.last != "\n" {
                    result.append("\n")
                }
                isAtLineStart = true
            }

            result.append(character)
            isAtLineStart = character == "\n"
        }

        return result
    }

    /// shell/JSON 경계를 거치며 글자 두 개 `\\n`으로 남은 줄바꿈을 Pretty에서만
    /// 복원한다. 원문은 저장된 그대로 남고, 코드 울타리 안의 escape도 코드이므로
    /// 건드리지 않는다. `\\\\n`처럼 escape된 역슬래시도 줄바꿈으로 오인하지 않는다.
    static func layoutText(_ source: String) -> String {
        var decoded = ""
        var cursor = source.startIndex
        var inCodeFence = false

        while cursor < source.endIndex {
            if source[cursor...].hasPrefix("```") {
                decoded.append("```")
                cursor = source.index(cursor, offsetBy: 3)
                inCodeFence.toggle()
                continue
            }

            let character = source[cursor]
            if character == "\\", !inCodeFence {
                let next = source.index(after: cursor)
                if next < source.endIndex {
                    if source[next] == "\\" {
                        decoded.append("\\\\")
                        cursor = source.index(after: next)
                        continue
                    }
                    if source[next] == "n" {
                        decoded.append("\n")
                        cursor = source.index(after: next)
                        continue
                    }
                    if source[next] == "r" {
                        var afterEscape = source.index(after: next)
                        if afterEscape < source.endIndex, source[afterEscape] == "\\" {
                            let possibleN = source.index(after: afterEscape)
                            if possibleN < source.endIndex, source[possibleN] == "n" {
                                afterEscape = source.index(after: possibleN)
                            }
                        }
                        decoded.append("\n")
                        cursor = afterEscape
                        continue
                    }
                }
            }
            decoded.append(character)
            cursor = source.index(after: cursor)
        }

        let normalized = decoded
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
        return splitInlineNumberedItems(normalized)
    }

    /// `요약 1) 첫째 2) 둘째`처럼 한 줄에 붙은 번호 목록을 블록 parser가
    /// 읽을 수 있는 줄로 만든다. `함수(1)` 같은 괄호 참조와 code fence는 둔다.
    private static func splitInlineNumberedItems(_ source: String) -> String {
        var result = ""
        var cursor = source.startIndex
        var inCodeFence = false

        while cursor < source.endIndex {
            if source[cursor...].hasPrefix("```") {
                result.append("```")
                cursor = source.index(cursor, offsetBy: 3)
                inCodeFence.toggle()
                continue
            }

            if !inCodeFence, source[cursor].isNumber,
               result.last == " " || result.last == "\t" {
                var end = cursor
                var digitCount = 0
                while end < source.endIndex, source[end].isNumber, digitCount < 4 {
                    digitCount += 1
                    end = source.index(after: end)
                }
                if digitCount <= 3, end < source.endIndex, source[end] == ")" {
                    let afterMarker = source.index(after: end)
                    if afterMarker < source.endIndex,
                       source[afterMarker] == " " || source[afterMarker] == "\t" {
                        while result.last == " " || result.last == "\t" {
                            result.removeLast()
                        }
                        result.append("\n")
                    }
                }
            }

            result.append(source[cursor])
            cursor = source.index(after: cursor)
        }
        return result
    }

    /// 형광펜과 인라인 코드까지 입힌 한 덩이. 블록 안쪽을 그릴 때 쓴다.
    ///
    /// 머리표 줄바꿈(`format`)은 여기서 안 돈다 — 표 칸이나 코드블록 안에서도
    /// 돌면 원문이 바뀐다. 그것이 필요한 문단에서만 미리 돌려 넘긴다.
    static func inlineText(_ source: String, seed: Int) -> AttributedString {
        var result = AttributedString()
        for piece in splitInlineCode(source) {
            switch piece {
            case let .plain(text):
                result.append(highlighted(text, seed: seed))
            case let .code(text):
                var styled = AttributedString(text)
                styled.font = .system(.body, design: .monospaced)
                // 파란 말풍선 위에서도 띠가 보여야 한다. 0.18 은 거기서 묻혔다.
                styled.backgroundColor = Color.secondary.opacity(0.28)
                result.append(styled)
            }
        }
        return result
    }

    private enum InlinePiece {
        case plain(String)
        case code(String)
    }

    /// `` `이런 것` `` 을 떼어낸다. 닫는 백틱이 없으면 글자 그대로 둔다.
    private static func splitInlineCode(_ source: String) -> [InlinePiece] {
        var pieces: [InlinePiece] = []
        var cursor = source.startIndex
        while let opening = source.range(of: "`", range: cursor..<source.endIndex) {
            guard let closing = source.range(
                of: "`", range: opening.upperBound..<source.endIndex
            ) else { break }
            let inner = String(source[opening.upperBound..<closing.lowerBound])
            // 빈 백틱 쌍은 코드가 아니다. 지우면 원문에 없던 일이 된다.
            if inner.isEmpty {
                cursor = opening.lowerBound
                break
            }
            pieces.append(.plain(String(source[cursor..<opening.lowerBound])))
            pieces.append(.code(inner))
            cursor = closing.upperBound
        }
        pieces.append(.plain(String(source[cursor...])))
        return pieces
    }

    /// 문단 한 덩이. 머리표 줄바꿈까지 돌린다.
    static func prettyText(_ source: String, seed: Int) -> AttributedString {
        inlineText(format(source), seed: seed)
    }

    /// 메시지 맨 앞의 `[역할이름]`. 없으면 nil.
    ///
    /// 에이전트들이 본문 첫머리에 자기 이름을 적는 습관이 있다. 화면에는 이미
    /// 발신자 이름과 역할이 나오므로 PM 에게는 없는 것이나 같은 정보다 —
    /// 지우지는 않고(자기들끼리 쓰는 것으로 보인다) 색만 낮춰 뒤로 물린다.
    static func leadingLabel(of text: String) -> String? {
        guard text.hasPrefix("["), let close = text.firstIndex(of: "]") else {
            return nil
        }
        let after = text.index(after: close)
        // `[글](주소)` 는 링크다. 링크 앞머리를 라벨로 잡으면 안 된다.
        if after < text.endIndex, text[after] == "(" { return nil }
        let label = text[text.startIndex...close]
        // 줄을 넘긴 대괄호는 이름이 아니다. 긴 것도 마찬가지다.
        guard !label.contains("\n"), label.count <= 24 else { return nil }
        return String(label)
    }

    /// `**...**` 를 형광펜으로. 표준 마크다운이면 굵게지만 여기서는 형광펜이다.
    private static func highlighted(_ formatted: String, seed: Int) -> AttributedString {
        var result = AttributedString()
        var cursor: String.Index = formatted.startIndex
        var highlightIndex = 0

        while let opening = formatted.range(of: "**", range: cursor..<formatted.endIndex) {
            result.append(AttributedString(String(formatted[cursor..<opening.lowerBound])))
            guard let closing = formatted.range(
                of: "**", range: opening.upperBound..<formatted.endIndex
            ) else {
                result.append(AttributedString(String(formatted[opening.lowerBound...])))
                return result
            }

            let content = String(formatted[opening.upperBound..<closing.lowerBound])
            if content.isEmpty {
                result.append(AttributedString("****"))
            } else {
                var highlighted = AttributedString(content)
                highlighted.backgroundColor = highlighterColor(
                    content: content, seed: seed + highlightIndex
                )
                highlighted.foregroundColor = Color.black.opacity(0.82)
                result.append(highlighted)
                highlightIndex += 1
            }
            cursor = closing.upperBound
        }

        result.append(AttributedString(String(formatted[cursor...])))
        return result
    }

    private static func highlighterColor(content: String, seed: Int) -> Color {
        let palette: [Color] = [
            Color(red: 1.00, green: 0.88, blue: 0.30),
            Color(red: 0.56, green: 0.93, blue: 0.70),
            Color(red: 1.00, green: 0.66, blue: 0.47),
            Color(red: 0.98, green: 0.62, blue: 0.78),
            Color(red: 0.50, green: 0.86, blue: 0.95),
        ]
        let scalarTotal = content.unicodeScalars.reduce(seed.magnitude) {
            $0 &+ UInt($1.value)
        }
        return palette[Int(scalarTotal % UInt(palette.count))].opacity(0.78)
    }
}
