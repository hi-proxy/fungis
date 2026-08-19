import SwiftUI

/// 코드 뷰어의 색. 어두운 바탕에 낮은 채도로, 오래 봐도 눈이 덜 아프게.
///
/// 쓰는 것만 둔다. 색칠기가 가르는 것이 넷뿐이라 팔레트도 그만큼이다.
enum CodeColors {
    static let background = Color(hex: 0x282A36)
    static let currentLine = Color(hex: 0x44475A)
    static let foreground = Color(hex: 0xF8F8F2)
    static let comment = Color(hex: 0x6272A4)
    static let orange = Color(hex: 0xFFB86C)
    static let pink = Color(hex: 0xFF79C6)
    static let purple = Color(hex: 0xBD93F9)
    static let yellow = Color(hex: 0xF1FA8C)
}

extension Color {
    init(hex: UInt32) {
        self.init(
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }
}

/// 코드에 색을 입힌다.
///
/// ponytail: 진짜 파서가 아니다. 주석·문자열·숫자·낱말만 가른다. 목적은 비서가
/// 짚어 준 줄을 눈으로 빨리 찾는 것이지 문법 검사가 아니다. 파서가 필요해지는
/// 날이 오면 그때 라이브러리를 붙인다 — 지금 그것 때문에 의존을 하나 더 지지
/// 않는다.
enum CodeHighlighter {
    /// 파일 한 장을 통째로 물들인다. 블록 주석이 줄을 넘으므로 줄마다 따로
    /// 하면 안 되고 위에서부터 상태를 이어가야 한다.
    static func paint(lines: [String], path: String) -> [AttributedString] {
        let rules = Rules(path: path)
        var inBlock = false
        return lines.map { line in
            let (painted, stillInBlock) = paint(line: line, rules: rules, inBlock: inBlock)
            inBlock = stillInBlock
            return painted
        }
    }

    private struct Rules {
        let lineComment: String
        let keywords: Set<String>
        let blocks: Bool

        init(path: String) {
            let ext = (path as NSString).pathExtension.lowercased()
            switch ext {
            case "py", "sh", "zsh", "rb", "yaml", "yml", "toml", "conf":
                lineComment = "#"
                blocks = false
            default:
                lineComment = "//"
                blocks = true
            }
            keywords = Self.keywords(for: ext)
        }

        static func keywords(for ext: String) -> Set<String> {
            let shared: Set<String> = [
                "if", "else", "for", "while", "return", "break", "continue",
                "class", "import", "try", "catch", "throw", "true", "false",
                "null", "nil", "in", "is", "as", "new", "case", "switch",
                "default", "do", "enum", "struct", "public", "private", "static",
            ]
            switch ext {
            case "py":
                return shared.union([
                    "def", "elif", "except", "finally", "from", "lambda", "None",
                    "True", "False", "with", "yield", "async", "await", "raise",
                    "pass", "not", "and", "or", "global", "assert", "del",
                ])
            case "swift":
                return shared.union([
                    "func", "let", "var", "guard", "extension", "protocol",
                    "init", "self", "some", "any", "async", "await", "throws",
                    "where", "defer", "typealias", "actor", "mutating", "override",
                ])
            default:
                return shared.union([
                    "func", "let", "var", "const", "function", "def", "async",
                    "await", "type", "interface", "export", "final", "void",
                ])
            }
        }
    }

    private static func paint(
        line: String, rules: Rules, inBlock: Bool
    ) -> (AttributedString, Bool) {
        var out = AttributedString()
        var open = inBlock
        var word = ""
        let characters = Array(line)
        var index = 0

        func flushWord() {
            guard !word.isEmpty else { return }
            var piece = AttributedString(word)
            if rules.keywords.contains(word) {
                piece.foregroundColor = CodeColors.pink
            } else if word.first?.isNumber == true {
                piece.foregroundColor = CodeColors.purple
            } else {
                piece.foregroundColor = CodeColors.foreground
            }
            out.append(piece)
            word = ""
        }

        func append(_ text: String, _ color: Color) {
            var piece = AttributedString(text)
            piece.foregroundColor = color
            out.append(piece)
        }

        while index < characters.count {
            let rest = String(characters[index...])

            if open {
                if let end = rest.range(of: "*/") {
                    let upto = String(rest[..<end.upperBound])
                    append(upto, CodeColors.comment)
                    index += upto.count
                    open = false
                } else {
                    append(rest, CodeColors.comment)
                    index = characters.count
                }
                continue
            }
            if rules.blocks, rest.hasPrefix("/*") {
                flushWord()
                open = true
                continue
            }
            if rest.hasPrefix(rules.lineComment) {
                flushWord()
                append(rest, CodeColors.comment)
                break
            }
            let character = characters[index]
            if character == "\"" || character == "'" {
                flushWord()
                var text = String(character)
                var scan = index + 1
                while scan < characters.count {
                    text.append(characters[scan])
                    if characters[scan] == character, characters[scan - 1] != "\\" { break }
                    scan += 1
                }
                append(text, CodeColors.yellow)
                index = scan + 1
                continue
            }
            if character.isLetter || character.isNumber || character == "_" {
                word.append(character)
            } else {
                flushWord()
                append(String(character), CodeColors.foreground.opacity(0.85))
            }
            index += 1
        }
        flushWord()
        return (out, open)
    }
}
