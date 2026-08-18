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

/// 이을 수 있는지 미리 본다. 누를 수는 있는데 아무 일도 안 일어나는 버튼이
/// 제일 나쁘다. 서버가 거절할 것은 누르기 전에 이유를 적고 잠근다.
///
/// 막는 것은 서버다. 여기는 사람이 헛손질하지 않게 하는 것뿐이라, 둘이
/// 어긋나면 서버가 옳다.
enum BoardGraph {
    static func refusal(
        source: BoardNode, target: BoardNode, nodes: [BoardNode]
    ) -> String? {
        if source.id == target.id { return "자기 자신이다" }
        if target.blockedBy.contains(source.id) { return "이미 이어져 있다" }
        if reaches(from: source.id, to: target.id, nodes: nodes) { return "순환이 된다" }
        return nil
    }

    /// source가 target을 이미 몇 단계 건너서라도 기다리고 있으면, 반대로 걸 때
    /// 순환이 된다. blockedBy는 기다리는 쪽의 것이다.
    static func reaches(from startID: String, to goalID: String, nodes: [BoardNode]) -> Bool {
        let byID = Dictionary(uniqueKeysWithValues: nodes.map { ($0.id, $0) })
        var stack = [startID]
        var seen: Set<String> = []
        while let current = stack.popLast() {
            if current == goalID { return true }
            guard seen.insert(current).inserted, let node = byID[current] else { continue }
            stack.append(contentsOf: node.blockedBy)
        }
        return false
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
    @State private var dropTargetID: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            if let source = linking { linkingBanner(source) }
            // 서버가 거절해도 화면이 아무 말을 안 했다. 순환이면 409가 오는데
            // 그것이 어디에도 안 뜨면 눌러도 안 되는 것과 구별이 안 된다.
            if let message = model.errorMessage {
                Label(message, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption).foregroundStyle(.orange)
                    .padding(.horizontal, 16).padding(.bottom, 8)
            }
            Divider()
            tracks
        }
        .frame(minWidth: 720, minHeight: 460)
        .task { await model.refreshBoard() }
    }

    /// 무엇을 잡았는지, 다음에 무엇을 눌러야 하는지 글자로 말한다. 점 색깔만
    /// 바뀌면 잡혔는지 아닌지를 사람이 알 수 없다.
    private func linkingBanner(_ source: BoardNode) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "arrow.right.circle.fill").foregroundStyle(.green)
            Text("‘\(source.title)’를 잡았다. 이 뒤에 올 카드의 ‘여기가 뒤’를 누른다.")
                .font(.caption)
            Spacer()
            Button("취소") { linking = nil }
                .buttonStyle(.plain).font(.caption)
                .contentShape(Rectangle())
        }
        .padding(.horizontal, 16).padding(.bottom, 10)
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
            // 선은 카드 뒤가 아니라 위에 얹는다. 카드가 겹쳐 있으면 선이 끊겨
            // 보이고, 끊긴 선은 없는 선과 같다.
            .overlayPreferenceValue(NodeBoundsKey.self) { anchors in
                GeometryReader { proxy in
                    ForEach(edges(), id: \.self) { edge in
                        if let from = anchors[edge.from], let to = anchors[edge.to] {
                            EdgeShape(from: proxy[from], to: proxy[to])
                                .stroke(
                                    Color.orange.opacity(0.75),
                                    style: StrokeStyle(lineWidth: 1.6, lineCap: .round)
                                )
                        }
                    }
                }
                .allowsHitTesting(false)
            }
        }
    }

    /// 선행에서 후행으로. blockedBy가 기다리는 쪽의 것이라 방향을 여기서 뒤집는다.
    private func edges() -> [BoardEdgeIDs] {
        model.board.tracks.flatMap(\.nodes).flatMap { node in
            node.blockedBy.map { BoardEdgeIDs(from: $0, to: node.id) }
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

    private func linkRefusal(source: BoardNode, target: BoardNode) -> String? {
        BoardGraph.refusal(
            source: source, target: target,
            nodes: model.board.tracks.flatMap(\.nodes)
        )
    }

    /// 왼쪽 점은 선행이 들어오는 표시고, 오른쪽 점이 잡는 자리다. 방향을
    /// 글자로 설명하지 않아도 좌우가 말한다.
    ///
    /// 받는 것은 점이 아니라 카드 안의 "여기가 뒤" 버튼이다. 점을 받는
    /// 자리로도 쓰니 무엇을 눌러야 하는지가 흐려졌다. 한 가지 일만 시킨다.
    @ViewBuilder
    private func linkHandle(_ node: BoardNode, leading: Bool) -> some View {
        if leading {
            Circle()
                .fill(node.state == "waiting" ? Color.orange : Color.secondary.opacity(0.3))
                .frame(width: 7, height: 7)
                .frame(width: 14)
                .help(node.state == "waiting" ? "선행이 있다" : "선행이 들어오는 자리")
        } else {
            let isSource = linking?.id == node.id
            Button {
                linking = isSource ? nil : node
            } label: {
                Circle()
                    .fill(isSource ? Color.green : Color.accentColor)
                    .frame(width: isSource ? 11 : 8, height: isSource ? 11 : 8)
                    // hit-area: 점은 손으로 못 맞춘다. 도형을 크게 준다.
                    .frame(width: 20, height: 22)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(isSource ? "잡힘. 다시 누르면 놓는다" : "잡는다. 이 카드가 앞이 된다")
        }
    }

    /// 점은 카드 위에 얹지 않고 옆에 둔다. 카드가 통째로 draggable이라
    /// 얹으면 카드의 드래그가 점의 드래그를 먹는다. 그래서 잇기가 안 됐다.
    private func nodeCard(_ node: BoardNode) -> some View {
        HStack(spacing: 3) {
            linkHandle(node, leading: true)
            nodeCardBody(node)
            linkHandle(node, leading: false)
        }
    }

    private func nodeCardBody(_ node: BoardNode) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(node.title).font(.callout).fixedSize(horizontal: false, vertical: true)
            // 끊는 자리는 기다리는 쪽에 둔다. 간선은 기다리는 쪽의 것이라
            // 여기 말고는 놓을 자리가 없다. 붙이는 길만 있고 떼는 길이 없으면
            // 한 번 잘못 이은 것을 되돌릴 수 없다.
            ForEach(node.blockedBy, id: \.self) { blockerID in
                blockerChip(node, blockerID: blockerID)
            }
            HStack(spacing: 8) {
                // 잡은 것이 있으면 카드마다 받는 자리를 크게 연다. 9pt 점 하나로는
                // 어디를 눌러야 하는지도, 눌리기는 한 것인지도 알 수 없다.
                if let source = linking, source.id != node.id {
                    let refusal = linkRefusal(source: source, target: node)
                    if let refusal {
                        Text(refusal).font(.caption2).foregroundStyle(.secondary)
                    }
                    Button("여기가 뒤") {
                        Task {
                            _ = await model.linkBoardNodes(
                                nodeID: node.id, waitsFor: source.id
                            )
                            linking = nil
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    .font(.caption2)
                    .disabled(refusal != nil)
                }
                Spacer()
                Button(role: .destructive) {
                    Task { _ = await model.removeBoardNode(nodeID: node.id) }
                } label: {
                    Image(systemName: "trash").font(.caption2)
                }
                .buttonStyle(.plain)
                .contentShape(Rectangle())
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        // 선을 그리려면 카드가 어디 있는지 알아야 한다. 자기 자리를 위로 올린다.
        .anchorPreference(key: NodeBoundsKey.self, value: .bounds) { [node.id: $0] }
        .background(cardColor(node), in: RoundedRectangle(cornerRadius: 10))
        .overlay {
            RoundedRectangle(cornerRadius: 10)
                .stroke(
                    dropTargetID == node.id ? Color.green
                        : (linking?.id == node.id ? Color.accentColor : Color.secondary.opacity(0.15)),
                    lineWidth: dropTargetID == node.id || linking?.id == node.id ? 2 : 1
                )
        }
        .draggable("move:\(node.id)")
        // 받는 자리. 여기 떨어진 카드가 선행이고 이 카드가 기다린다.
        .dropDestination(for: String.self) { items, _ in
            // 카드 위에 떨어뜨려도 그 열로 옮겨진다. 카드가 열을 덮고 있어서
            // 여기서 안 받으면 카드마다 죽은 구역이 된다.
            guard let payload = items.first else { return false }
            if let movedID = Self.moved(payload), movedID != node.id {
                Task {
                    _ = await model.setBoardNodeStatus(nodeID: movedID, status: node.status)
                }
                return true
            }
            return false
        } isTargeted: { targeted in
            // 받을 수 있는 자리인지 손이 알아야 한다. 놓아 보고 나서야 아는 건
            // 안 되는 것과 구별이 안 된다.
            dropTargetID = targeted ? node.id : (dropTargetID == node.id ? nil : dropTargetID)
        }
        .contextMenu {
            // 드래그가 안 되는 상황에도 길은 남긴다.
            Button(linking?.id == node.id ? "잇기 취소" : "잇기 시작") {
                linking = linking?.id == node.id ? nil : node
            }
        }
    }

    private func blockerChip(_ node: BoardNode, blockerID: String) -> some View {
        HStack(spacing: 4) {
            Image(systemName: "clock").font(.caption2)
            Text(blockerTitle(blockerID)).font(.caption2)
                .fixedSize(horizontal: false, vertical: true)
            Button {
                Task {
                    _ = await model.unlinkBoardNodes(nodeID: node.id, waitsFor: blockerID)
                }
            } label: {
                Image(systemName: "xmark").font(.system(size: 8, weight: .bold))
                    // hit-area: 8pt 글리프는 손으로 못 맞춘다.
                    .frame(width: 16, height: 16)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help("이 선을 끊는다")
        }
        .foregroundStyle(.orange)
        .padding(.horizontal, 6).padding(.vertical, 2)
        .background(.orange.opacity(0.12), in: Capsule())
    }

    private func blockerTitle(_ id: String) -> String {
        model.board.tracks.flatMap(\.nodes).first { $0.id == id }?.title ?? "다른 방의 일"
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


struct BoardEdgeIDs: Hashable {
    let from: String
    let to: String
}

private struct NodeBoundsKey: PreferenceKey {
    static let defaultValue: [String: Anchor<CGRect>] = [:]

    static func reduce(
        value: inout [String: Anchor<CGRect>],
        nextValue: () -> [String: Anchor<CGRect>]
    ) {
        value.merge(nextValue()) { _, next in next }
    }
}

/// 선행 카드의 오른쪽에서 나와 후행 카드의 왼쪽으로 들어간다. 가로로 빠졌다가
/// 들어오게 굽혀서, 트랙이 여럿이어도 어느 카드에서 나왔는지가 보이게 한다.
private struct EdgeShape: Shape {
    let from: CGRect
    let to: CGRect

    func path(in _: CGRect) -> Path {
        let start = CGPoint(x: from.maxX + 3, y: from.midY)
        let end = CGPoint(x: to.minX - 3, y: to.midY)
        let reach = max(28, abs(end.x - start.x) * 0.45)
        var path = Path()
        path.move(to: start)
        path.addCurve(
            to: end,
            control1: CGPoint(x: start.x + reach, y: start.y),
            control2: CGPoint(x: end.x - reach, y: end.y)
        )
        return path
    }
}
