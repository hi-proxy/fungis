import Foundation

/// 메시지 본문 한 덩이. 문단 하나, 표 하나, 코드블록 하나가 각각 이것이다.
///
/// 본문 전체를 `Text` 하나로 그리면 표가 원리상 안 된다 — 열을 맞추려면
/// 칸이 따로 재어져야 한다. 그래서 그리기 전에 여기서 가른다.
enum MessageBlock: Hashable {
    case paragraph(String)
    /// `#` 개수. 1~6.
    case heading(Int, String)
    case bullets([MessageBulletItem])
    case quote(String)
    /// 안의 원문은 손대지 않는다. 코드가 바뀌면 코드가 아니다.
    case code(String)
    case table(header: [String], alignments: [MessageColumnAlignment], rows: [[String]])
    case rule
}

struct MessageBulletItem: Hashable {
    /// 들여쓰기 깊이. 두 칸이 한 단계다.
    let depth: Int
    /// `1.` 처럼 앞에 적을 것. nil 이면 번호 없는 항목이다.
    let marker: String?
    let text: String
}

enum MessageColumnAlignment: Hashable {
    case leading, center, trailing
}

enum MessageBlockParser {
    /// 본문을 블록으로 가른다.
    ///
    /// 마크다운 전부를 읽지 않는다. 에이전트와 PM 이 실제로 치는 것만 읽고,
    /// 못 읽은 줄은 문단으로 흘려보낸다 — 모르는 문법을 만나도 글자는 남는다.
    static func blocks(of source: String) -> [MessageBlock] {
        let lines = MessagePrettyPrinter.layoutText(source)
            .components(separatedBy: "\n")

        var blocks: [MessageBlock] = []
        var paragraph: [String] = []
        var index = 0

        func flushParagraph() {
            let joined = paragraph.joined(separator: "\n")
            paragraph.removeAll()
            let trimmed = joined.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty { blocks.append(.paragraph(joined)) }
        }

        while index < lines.count {
            let line = lines[index]
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            // 코드블록이 먼저다. 안에서는 어떤 문법도 읽지 않는다 — 표처럼
            // 생긴 줄도, 머리표도 코드블록 안에서는 그냥 글자다.
            if trimmed.hasPrefix("```") {
                flushParagraph()
                var body: [String] = []
                index += 1
                while index < lines.count,
                      !lines[index].trimmingCharacters(in: .whitespaces).hasPrefix("```") {
                    body.append(lines[index])
                    index += 1
                }
                // 닫는 울타리가 없어도 남은 줄을 코드로 그린다. 열다 만 것을
                // 문단으로 되돌리면 그 안의 표·머리표가 다시 읽혀서 더 망가진다.
                if index < lines.count { index += 1 }
                blocks.append(.code(body.joined(separator: "\n")))
                continue
            }

            if trimmed.isEmpty {
                flushParagraph()
                index += 1
                continue
            }

            if isRule(trimmed) {
                flushParagraph()
                blocks.append(.rule)
                index += 1
                continue
            }

            if let heading = heading(of: trimmed) {
                flushParagraph()
                blocks.append(heading)
                index += 1
                continue
            }

            // 표는 두 줄을 같이 봐야 안다. 구분선이 없으면 그냥 파이프를 쓴
            // 문장이다 — `a | b` 를 표로 만들면 멀쩡한 문장이 칸으로 쪼개진다.
            if index + 1 < lines.count,
               let table = table(header: line, divider: lines[index + 1],
                                 rest: lines[(index + 2)...]) {
                flushParagraph()
                blocks.append(table.block)
                index += 2 + table.consumedRows
                continue
            }

            if bulletItem(of: line) != nil {
                flushParagraph()
                var items: [MessageBulletItem] = []
                while index < lines.count, let item = bulletItem(of: lines[index]) {
                    items.append(item)
                    index += 1
                }
                blocks.append(.bullets(items))
                continue
            }

            if trimmed.hasPrefix(">") {
                flushParagraph()
                var quoted: [String] = []
                while index < lines.count,
                      lines[index].trimmingCharacters(in: .whitespaces).hasPrefix(">") {
                    let stripped = lines[index]
                        .trimmingCharacters(in: .whitespaces)
                        .dropFirst()
                    quoted.append(
                        String(stripped).hasPrefix(" ")
                            ? String(stripped.dropFirst()) : String(stripped)
                    )
                    index += 1
                }
                blocks.append(.quote(quoted.joined(separator: "\n")))
                continue
            }

            paragraph.append(line)
            index += 1
        }

        flushParagraph()
        return blocks
    }

    // MARK: 줄 하나 읽기

    private static func isRule(_ trimmed: String) -> Bool {
        guard trimmed.count >= 3 else { return false }
        return trimmed.allSatisfy { $0 == "-" }
            || trimmed.allSatisfy { $0 == "*" }
            || trimmed.allSatisfy { $0 == "_" }
    }

    private static func heading(of trimmed: String) -> MessageBlock? {
        let hashes = trimmed.prefix(while: { $0 == "#" }).count
        guard hashes >= 1, hashes <= 6 else { return nil }
        let rest = trimmed.dropFirst(hashes)
        // `#태그` 는 헤딩이 아니다. 공백이 있어야 헤딩이다.
        guard rest.hasPrefix(" ") else { return nil }
        return .heading(hashes, rest.trimmingCharacters(in: .whitespaces))
    }

    private static func bulletItem(of line: String) -> MessageBulletItem? {
        let indent = line.prefix(while: { $0 == " " || $0 == "\t" }).count
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        // 두 칸을 한 단계로 센다. 탭 하나도 한 단계다.
        let depth = min(indent / 2, 4)

        for marker in ["- ", "* ", "• "] where trimmed.hasPrefix(marker) {
            return MessageBulletItem(
                depth: depth, marker: nil,
                text: String(trimmed.dropFirst(marker.count))
            )
        }
        // `1. ` `12) ` 같은 것. 숫자가 아니면 목록이 아니다.
        let digits = trimmed.prefix(while: \.isNumber)
        guard !digits.isEmpty, digits.count <= 3 else { return nil }
        let after = trimmed.dropFirst(digits.count)
        guard after.hasPrefix(". ") || after.hasPrefix(") ") else { return nil }
        return MessageBulletItem(
            depth: depth, marker: String(digits) + ".",
            text: String(after.dropFirst(2))
        )
    }

    // MARK: 표

    private static func table(
        header: String, divider: String, rest: ArraySlice<String>
    ) -> (block: MessageBlock, consumedRows: Int)? {
        let headerCells = cells(of: header)
        let dividerCells = cells(of: divider)
        guard headerCells.count >= 2, dividerCells.count == headerCells.count else {
            return nil
        }
        var alignments: [MessageColumnAlignment] = []
        for cell in dividerCells {
            guard let alignment = alignment(of: cell) else { return nil }
            alignments.append(alignment)
        }

        var rows: [[String]] = []
        for line in rest {
            let row = cells(of: line)
            // 파이프가 없는 줄에서 표가 끝난다. 칸 수가 달라도 받아 준다 —
            // 한 칸 빠뜨렸다고 나머지 표를 통째로 버리면 손해가 더 크다.
            guard row.count >= 2 else { break }
            rows.append(padded(row, to: headerCells.count))
        }
        return (
            .table(header: headerCells, alignments: alignments, rows: rows),
            rows.count
        )
    }

    /// `| a | b |` 를 칸으로 가른다. 파이프가 없으면 빈 배열이다.
    private static func cells(of line: String) -> [String] {
        var trimmed = line.trimmingCharacters(in: .whitespaces)
        guard trimmed.contains("|") else { return [] }
        if trimmed.hasPrefix("|") { trimmed.removeFirst() }
        if trimmed.hasSuffix("|") { trimmed.removeLast() }
        return trimmed
            .components(separatedBy: "|")
            .map { $0.trimmingCharacters(in: .whitespaces) }
    }

    private static func alignment(of cell: String) -> MessageColumnAlignment? {
        let body = cell.trimmingCharacters(in: .whitespaces)
        guard body.count >= 3 else { return nil }
        let left = body.hasPrefix(":")
        let right = body.hasSuffix(":")
        let dashes = body.dropFirst(left ? 1 : 0).dropLast(right ? 1 : 0)
        guard !dashes.isEmpty, dashes.allSatisfy({ $0 == "-" }) else { return nil }
        if left && right { return .center }
        return right ? .trailing : .leading
    }

    private static func padded(_ row: [String], to width: Int) -> [String] {
        row.count >= width
            ? Array(row.prefix(width))
            : row + Array(repeating: "", count: width - row.count)
    }
}
