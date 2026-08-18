import SwiftUI

/// 타임라인 위에 늘 떠 있는 한 줄. PM이 어느 방에 있든 전체를 본다.
///
/// 프로젝트별로 자르지 않는다. 이 띠의 주 독자는 PM이고, PM이 원한 것은
/// "모든 프로젝트를 조망하는 시야"였다. 지금 방만 도드라지게 해서 lead에게도
/// 자기 것이 먼저 보이게 한다.
struct BoardStrip: View {
    let tracks: [BoardTrack]
    let currentProjectID: String
    let open: () -> Void

    var body: some View {
        Button(action: open) {
            HStack(spacing: 10) {
                Image(systemName: "square.stack.3d.up")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if tracks.isEmpty {
                    Text("보드가 비어 있음 — 눌러서 프로젝트 붙이기")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(tracks) { track in
                                summary(for: track)
                                if track.id != tracks.last?.id {
                                    Text("·").foregroundStyle(.quaternary)
                                }
                            }
                        }
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity)
            .background(.quaternary.opacity(0.25))
            // hit-area: 띠 전체가 눌린다. 글자만 눌리면 못 찾는다.
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help("상황보드 열기")
    }

    private func summary(for track: BoardTrack) -> some View {
        let here = track.projectID == currentProjectID
        return HStack(spacing: 4) {
            Text(track.projectName)
                .fontWeight(here ? .semibold : .regular)
            Text(BoardSummary.label(for: track, in: tracks))
                .foregroundStyle(here ? .primary : .secondary)
        }
        .font(.caption)
        .opacity(here ? 1 : 0.75)
    }
}

/// 트랙 하나를 한 낱말로 줄인다. 막힌 것이 있으면 그것부터 말한다 —
/// 띠에서 알고 싶은 것은 "지금 무엇이 멈춰 있나"다.
enum BoardSummary {
    static func label(for track: BoardTrack, in tracks: [BoardTrack]) -> String {
        if let blocked = track.nodes.first(where: { $0.state == "waiting" }) {
            let names = blockerNames(of: blocked, in: tracks)
            return names.isEmpty ? "대기" : "대기(\(names.joined(separator: ",")))"
        }
        if track.nodes.contains(where: { $0.state == "active" }) { return "하는중" }
        if track.nodes.isEmpty { return "비어있음" }
        if track.nodes.allSatisfy({ $0.state == "done" }) { return "끝" }
        return "안시작"
    }

    static func blockerNames(of node: BoardNode, in tracks: [BoardTrack]) -> [String] {
        let byID = Dictionary(
            uniqueKeysWithValues: tracks.flatMap { track in
                track.nodes.map { ($0.id, track.projectName) }
            }
        )
        var seen: [String] = []
        for blockerID in node.blockedBy {
            if let name = byID[blockerID], !seen.contains(name) { seen.append(name) }
        }
        return seen
    }
}

/// 손대는 자리. 늘 떠 있는 것은 띠고, 고치는 것은 여기서 한다.
struct BoardSheet: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var draftTitles: [String: String] = [:]
    @State private var linking: BoardNode?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            tracks
        }
        .frame(minWidth: 720, minHeight: 460)
        .task { await model.refreshBoard() }
    }

    private var header: some View {
        HStack {
            Text(model.board.hq?.name ?? "상황보드").font(.title2.bold())
            Spacer()
            Button("닫기") { dismiss() }
        }
        .padding(16)
    }

    private static let columns: [(status: String, title: String)] = [
        ("todo", "안 시작"), ("active", "하는 중"), ("done", "끝"),
    ]

    private var tracks: some View {
        ScrollView([.horizontal, .vertical]) {
            VStack(alignment: .leading, spacing: 0) {
                columnHeader
                ForEach(model.board.tracks) { track in
                    Divider()
                    trackSection(track)
                }
            }
            .padding(20)
        }
    }

    private var columnHeader: some View {
        HStack(alignment: .bottom, spacing: 12) {
            Text("").frame(width: 150, alignment: .leading)
            ForEach(Self.columns, id: \.status) { column in
                Text(column.title)
                    .font(.caption.bold())
                    .foregroundStyle(.secondary)
                    .frame(width: 220, alignment: .leading)
            }
        }
        .padding(.bottom, 8)
    }

    /// 트랙 하나가 가로 한 줄이고 상태가 열이다. 상태를 드롭다운으로 두면
    /// 한 줄씩 눌러 봐야 어디까지 왔는지 알 수 있다. 한 눈에 보라고 만든
    /// 물건이 그러면 안 된다.
    private func trackSection(_ track: BoardTrack) -> some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(track.projectName).font(.headline)
                Button("떼기") {
                    Task { _ = await model.disconnectTrack(projectID: track.projectID) }
                }
                .buttonStyle(.plain)
                .font(.caption)
                // hit-area: 글자만 눌리지 않게 도형을 준다.
                .contentShape(Rectangle())
                .foregroundStyle(.secondary)
            }
            .frame(width: 150, alignment: .leading)

            ForEach(Self.columns, id: \.status) { column in
                columnCell(track, status: column.status)
            }
        }
        .padding(.vertical, 10)
    }

    private func columnCell(_ track: BoardTrack, status: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(track.nodes.filter { $0.status == status }) { node in
                nodeCard(node)
            }
            if status == "todo" { addNodeField(track) }
        }
        .frame(width: 220, alignment: .top)
        .frame(maxHeight: .infinity, alignment: .top)
        .dropDestination(for: String.self) { items, _ in
            // 잇기 드래그가 열에 떨어지면 무시한다. 같은 String이라 접두사로 가른다.
            guard let payload = items.first,
                  let nodeID = Self.moved(payload) else { return false }
            Task { _ = await model.setBoardNodeStatus(nodeID: nodeID, status: status) }
            return true
        }
    }

    /// 끄는 동작이 둘이라 접두사로 가른다. 몸통을 끌면 상태가 옮겨가고
    /// 오른쪽 점을 끌면 선행이 된다.
    private static func moved(_ payload: String) -> String? {
        payload.hasPrefix("move:") ? String(payload.dropFirst(5)) : nil
    }

    private static func linked(_ payload: String) -> String? {
        payload.hasPrefix("link:") ? String(payload.dropFirst(5)) : nil
    }

    /// 왼쪽이 선행이고 오른쪽이 후행이다. 오른쪽 점을 잡아 다른 카드에
    /// 떨어뜨리면 그 카드가 이것을 기다린다. 방향을 글자로 설명하지 않아도
    /// 손이 알게 한다.
    private func linkHandle(_ node: BoardNode, leading: Bool) -> some View {
        Circle()
            .fill(leading ? Color.secondary.opacity(0.35) : Color.accentColor)
            .frame(width: 9, height: 9)
            .contentShape(Circle().inset(by: -6))
    }

    private func nodeCard(_ node: BoardNode) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(node.title).font(.callout).fixedSize(horizontal: false, vertical: true)
            if node.state == "waiting" {
                let names = BoardSummary.blockerNames(of: node, in: model.board.tracks)
                Label(names.joined(separator: ", "), systemImage: "clock")
                    .font(.caption2).foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 8) {
                Spacer()
                Button(role: .destructive) {
                    Task { _ = await model.removeBoardNode(nodeID: node.id) }
                } label: {
                    Image(systemName: "trash").font(.caption2)
                }
                .buttonStyle(.plain)
                .contentShape(Rectangle())
            }
            linkTargetHint(for: node)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(cardColor(node), in: RoundedRectangle(cornerRadius: 10))
        .overlay {
            RoundedRectangle(cornerRadius: 10)
                .stroke(
                    linking?.id == node.id ? Color.accentColor : Color.secondary.opacity(0.15),
                    lineWidth: linking?.id == node.id ? 2 : 1
                )
        }
        .draggable("move:\(node.id)")
        // 받는 자리. 여기 떨어진 카드가 선행이고 이 카드가 기다린다.
        .dropDestination(for: String.self) { items, _ in
            guard let payload = items.first else { return false }
            if let sourceID = Self.linked(payload) {
                guard sourceID != node.id else { return false }
                Task { _ = await model.linkBoardNodes(nodeID: node.id, waitsFor: sourceID) }
                return true
            }
            // 카드 위에 떨어뜨려도 그 열로 옮겨진다. 카드가 열을 덮고 있어서
            // 여기서 안 받으면 카드마다 죽은 구역이 된다.
            if let movedID = Self.moved(payload), movedID != node.id {
                Task {
                    _ = await model.setBoardNodeStatus(nodeID: movedID, status: node.status)
                }
                return true
            }
            return false
        }
        .overlay(alignment: .leading) {
            linkHandle(node, leading: true).offset(x: -4.5)
        }
        .overlay(alignment: .trailing) {
            linkHandle(node, leading: false)
                .offset(x: 4.5)
                .draggable("link:\(node.id)")
                .help("끌어서 이 뒤에 올 카드에 놓는다")
        }
        .contextMenu {
            // 드래그가 안 되는 상황에도 길은 남긴다.
            Button(linking?.id == node.id ? "잇기 취소" : "잇기 시작") {
                linking = linking?.id == node.id ? nil : node
            }
        }
    }

    private func cardColor(_ node: BoardNode) -> some ShapeStyle {
        node.state == "waiting"
            ? AnyShapeStyle(.orange.opacity(0.12))
            : AnyShapeStyle(.quaternary.opacity(0.3))
    }

    private func addNodeField(_ track: BoardTrack) -> some View {
        HStack(spacing: 6) {
            TextField(
                "올릴 것",
                text: Binding(
                    get: { draftTitles[track.projectID] ?? "" },
                    set: { draftTitles[track.projectID] = $0 }
                )
            )
            .textFieldStyle(.plain)
            .onSubmit { addNode(to: track) }
            Button("올리기") { addNode(to: track) }
                .buttonStyle(.plain).font(.caption2)
                .contentShape(Rectangle())
                .disabled(draft(track).isEmpty)
        }
        .padding(8)
        .background(.quaternary.opacity(0.15), in: RoundedRectangle(cornerRadius: 8))
    }

    /// 잇기를 누른 노드가 있으면, 나머지 노드가 "여기를 기다린다"로 바뀐다.
    /// 선행은 기다리는 쪽의 것이라 방향이 헷갈리지 않게 문구로 못박는다.
    @ViewBuilder
    private func linkTargetHint(for node: BoardNode) -> some View {
        if let source = linking, source.id != node.id {
            HStack {
                Spacer()
                Button("\(source.title)가 이것을 기다림") {
                    Task {
                        _ = await model.linkBoardNodes(
                            nodeID: source.id, waitsFor: node.id
                        )
                        linking = nil
                    }
                }
                .font(.caption)
                .buttonStyle(.borderedProminent)
            }
        }
    }

    // MARK: 소집
    //
    // 전체 목록을 보여주고 체크로 고른다. 안 붙은 것만 보여주면 지금 누가
    // 들어와 있는지가 안 보이고, lead 여부도 눌러봐야 안다.


    private func draft(_ track: BoardTrack) -> String {
        (draftTitles[track.projectID] ?? "").trimmingCharacters(in: .whitespaces)
    }

    private func addNode(to track: BoardTrack) {
        let title = draft(track)
        guard !title.isEmpty else { return }
        Task {
            if await model.addBoardNode(projectID: track.projectID, title: title) {
                draftTitles[track.projectID] = ""
            }
        }
    }
}
