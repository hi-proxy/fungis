import SwiftUI

/// 메시지 본문을 그린다.
///
/// **표만 따로 그리고 나머지는 한 덩이로 둔다.** 표는 칸이 따로 재어져야 열이
/// 맞아서 뷰로 나뉠 수밖에 없지만, `Text` 가 나뉘면 그 경계를 넘어 긁을 수
/// 없다 — SwiftUI 의 선택은 `Text` 하나 안에서만 이어진다.
///
/// 처음에는 블록마다 뷰를 뒀다가 되돌렸다(`63678e5` → 이 파일). 표 하나
/// 예쁘게 그리자고 메시지 전체를 긁는 것을 잃는 거래였고, 긁기는 매일 쓰고
/// 표는 가끔 온다.
struct MessageBodyView: View {
    let source: String
    let seed: Int
    /// 내 말풍선은 바탕이 진해서 옅은 회색 배경이 안 보인다.
    let isMine: Bool
    @AppStorage(BodyScale.key) private var step = BodyScale.normal

    /// 그릴 조각. 표가 경계고, 표 사이의 모든 블록은 한 덩이로 합쳐진다.
    private enum Piece {
        case text(AttributedString)
        case table(
            header: [String], alignments: [MessageColumnAlignment], rows: [[String]]
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            ForEach(Array(pieces.enumerated()), id: \.offset) { _, piece in
                view(for: piece)
            }
        }
        .textSelection(.enabled)
        .fixedSize(horizontal: false, vertical: true)
    }

    @ViewBuilder private func view(for piece: Piece) -> some View {
        switch piece {
        case let .text(text):
            Text(text)
        case let .table(header, alignments, rows):
            table(header: header, alignments: alignments, rows: rows)
        }
    }

    private var pieces: [Piece] {
        var pieces: [Piece] = []
        var buffer = AttributedString()

        func flush() {
            guard !buffer.characters.isEmpty else { return }
            pieces.append(.text(buffer))
            buffer = AttributedString()
        }

        for (index, block) in MessageBlockParser.blocks(of: source).enumerated() {
            if case let .table(header, alignments, rows) = block {
                flush()
                pieces.append(.table(header: header, alignments: alignments, rows: rows))
                continue
            }
            if !buffer.characters.isEmpty { buffer.append(AttributedString("\n\n")) }
            buffer.append(attributed(block, isFirst: index == 0))
        }
        flush()
        return pieces
    }

    // MARK: 한 덩이로 접기

    private func attributed(_ block: MessageBlock, isFirst: Bool) -> AttributedString {
        switch block {
        case let .paragraph(text):
            // 문단에서만 머리표 줄바꿈이 돈다. 표 칸이나 코드블록 안에서 돌면
            // 원문이 바뀐다.
            return paragraph(text, isFirst: isFirst)

        case let .heading(level, text):
            var painted = MessagePrettyPrinter.inlineText(text, seed: seed)
            painted.font = BodyScale.font(level <= 2 ? .title3 : .body, step).bold()
            return painted

        case let .bullets(items):
            var painted = AttributedString()
            for (index, item) in items.enumerated() {
                if index > 0 { painted.append(AttributedString("\n")) }
                // 들여쓰기를 공백으로 넣는다. 뷰로 밀면 그만큼 Text 가 갈린다.
                var head = AttributedString(
                    String(repeating: "  ", count: item.depth)
                        + (item.marker ?? "•") + " "
                )
                head.foregroundColor = dimColor
                painted.append(head)
                painted.append(MessagePrettyPrinter.inlineText(item.text, seed: seed))
            }
            return painted

        case let .quote(text):
            // 세로 막대를 못 쓴다. 기울임과 낮춘 색으로 대신한다.
            var painted = MessagePrettyPrinter.inlineText(text, seed: seed)
            painted.font = BodyScale.font(.body, step).italic()
            painted.foregroundColor = dimColor
            return painted

        case let .code(text):
            var painted = AttributedString(text)
            painted.font = BodyScale.font(.callout, step, monospaced: true)
            painted.backgroundColor = plateColor
            return painted

        case .rule:
            var painted = AttributedString(String(repeating: "─", count: 24))
            painted.foregroundColor = dimColor
            return painted

        case .table:
            // 표는 여기 안 온다. 위에서 조각으로 갈린다.
            return AttributedString()
        }
    }

    /// 첫 문단. 맨 앞의 `[역할이름]` 은 색을 낮춰 뒤로 물린다.
    ///
    /// 지우지 않는 이유는 그것이 에이전트들끼리 쓰는 표시로 보여서다. 읽는
    /// 사람에게는 이미 말풍선 위에 있는 정보라 앞자리를 내줄 필요가 없다.
    private func paragraph(_ text: String, isFirst: Bool) -> AttributedString {
        var painted = MessagePrettyPrinter.prettyText(text, seed: seed)
        // 인라인 코드가 백틱을 떼면 칠한 쪽이 원문보다 짧을 수 있다. 그 차이로
        // 범위를 넘으면 그 자리에서 죽는다.
        guard isFirst, let label = MessagePrettyPrinter.leadingLabel(of: text),
              painted.characters.count >= label.count
        else { return painted }
        let end = painted.index(painted.startIndex, offsetByCharacters: label.count)
        painted[painted.startIndex..<end].foregroundColor = dimColor
        return painted
    }

    /// 본문보다 한 단계 뒤로 물리는 색.
    private var dimColor: Color {
        isMine ? Color.white.opacity(0.55) : Color.secondary
    }

    /// 말풍선 바탕 위에 한 겹 얹는 색. 내 말풍선은 진해서 옅은 회색이 안 보인다.
    private var plateColor: Color {
        isMine ? Color.black.opacity(0.22) : Color.secondary.opacity(0.12)
    }

    // MARK: 표

    @ViewBuilder private func table(
        header: [String], alignments: [MessageColumnAlignment], rows: [[String]]
    ) -> some View {
        Grid(alignment: .topLeading, horizontalSpacing: 0, verticalSpacing: 0) {
            GridRow {
                ForEach(Array(header.enumerated()), id: \.offset) { column, cell in
                    cellText(cell, alignment: alignment(alignments, column))
                        .fontWeight(.semibold)
                }
            }
            .background(plateColor)

            ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                Divider().gridCellUnsizedAxes(.horizontal)
                GridRow {
                    ForEach(Array(row.enumerated()), id: \.offset) { column, cell in
                        cellText(cell, alignment: alignment(alignments, column))
                    }
                }
                // 줄무늬. 칸이 비어 있어도 어느 행에 속하는지 보인다.
                .background(index.isMultiple(of: 2) ? Color.clear : plateColor.opacity(0.45))
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay {
            RoundedRectangle(cornerRadius: 8)
                .stroke(
                    isMine ? Color.white.opacity(0.28) : Color.secondary.opacity(0.25),
                    lineWidth: 1
                )
        }
    }

    /// 구분선이 헤더보다 짧게 적힌 표가 있다. 없는 칸은 왼쪽 정렬로 둔다.
    private func alignment(
        _ alignments: [MessageColumnAlignment], _ column: Int
    ) -> MessageColumnAlignment {
        column < alignments.count ? alignments[column] : .leading
    }

    private func cellText(
        _ cell: String, alignment: MessageColumnAlignment
    ) -> some View {
        Text(MessagePrettyPrinter.inlineText(cell, seed: seed))
            .multilineTextAlignment(textAlignment(alignment))
            .frame(maxWidth: .infinity, alignment: frameAlignment(alignment))
            .padding(.horizontal, 9).padding(.vertical, 5)
    }

    private func textAlignment(_ alignment: MessageColumnAlignment) -> TextAlignment {
        switch alignment {
        case .leading: .leading
        case .center: .center
        case .trailing: .trailing
        }
    }

    private func frameAlignment(_ alignment: MessageColumnAlignment) -> Alignment {
        switch alignment {
        case .leading: .leading
        case .center: .center
        case .trailing: .trailing
        }
    }
}
