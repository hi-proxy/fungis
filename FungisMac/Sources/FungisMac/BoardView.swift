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

    /// 선행이 안 끝났으면 하는 중으로 못 옮긴다. 이 규칙이 없으면 보드는
    /// 순서를 적어두기만 하고 아무것도 지키지 않는다.
    ///
    /// 끝으로 옮기는 것은 막지 않는다. 이미 해버린 일을 보드가 못 했다고
    /// 우기면 사람이 보드를 버린다.
    static func blocksStarting(
        _ node: BoardNode, to status: String, nodes: [BoardNode]
    ) -> BoardNode? {
        guard status == "active" else { return nil }
        let byID = Dictionary(uniqueKeysWithValues: nodes.map { ($0.id, $0) })
        return node.blockedBy.compactMap { byID[$0] }.first { $0.status != "done" }
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
        return "예정"
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
/// 손대는 자리. 늘 떠 있는 것은 띠고, 고치는 것은 여기서 한다.
///
/// 행이 프로젝트고 열이 상태다. 선은 카드 뒤에 그린다 — 앞에 그으면 글자를
/// 가리고, 가려진 선은 없는 선과 같다. 프로젝트를 넘는 선만 굵게 긋는다.
/// HQ가 존재하는 이유가 거기라서, 그것만 눈에 먼저 들어와야 한다.
struct BoardSheet: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var draftTitles: [String: String] = [:]
    @State private var linking: BoardNode?
    @State private var hoveredCardID: String?
    @State private var showEdges = true
    @State private var onlyLinked = false

    private static let columns: [(status: String, title: String)] = [
        ("todo", "예정"), ("active", "하는 중"), ("done", "끝"),
    ]

    private static let trackWidth: CGFloat = 150
    private static let columnWidth: CGFloat = 300

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            if let source = linking { linkingBanner(source) }
            if let message = model.errorMessage {
                Label(message, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption).foregroundStyle(.orange)
                    .padding(.horizontal, 20).padding(.bottom, 8)
            }
            Divider()
            tracks
            Divider()
            legend
        }
        .frame(minWidth: 1160, minHeight: 560)
        .task { await model.refreshBoard() }
        // 잡은 것을 놓는 길이 버튼 하나뿐이면 손이 갇힌다.
        .onExitCommand { linking = nil }
    }

    // MARK: 머리와 발

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(model.board.hq?.name ?? "상황보드").font(.title2.bold())
            Text(summaryLine)
                .font(.caption).foregroundStyle(.secondary)
            Spacer()
            Toggle("선 보기", isOn: $showEdges)
                .toggleStyle(.checkbox).font(.caption)
            Toggle("선행 있는 것만", isOn: $onlyLinked)
                .toggleStyle(.checkbox).font(.caption)
            Button("닫기") { dismiss() }
        }
        .padding(.horizontal, 20).padding(.top, 16).padding(.bottom, 12)
    }

    private var summaryLine: String {
        let nodes = allNodes
        let edgeCount = nodes.reduce(0) { $0 + $1.blockedBy.count }
        return "\(model.board.tracks.count) 프로젝트 · 티켓 \(nodes.count) · 선행 \(edgeCount)"
    }

    private var legend: some View {
        HStack(spacing: 16) {
            legendItem(color: .green, text: "선행 충족")
            legendItem(color: .orange, text: "선행 대기")
            legendItem(color: .accentColor, text: "프로젝트 넘음")
            Spacer()
            if linking != nil {
                Text("esc 취소").font(.caption2).foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 20).padding(.vertical, 8)
    }

    private func legendItem(color: Color, text: String) -> some View {
        HStack(spacing: 5) {
            Capsule().fill(color).frame(width: 14, height: 3)
            Text(text).font(.caption2).foregroundStyle(.secondary)
        }
    }

    private func linkingBanner(_ source: BoardNode) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "arrow.right.circle.fill").foregroundStyle(.green)
            Text("‘\(source.title)’를 잡았다. 이 뒤에 올 카드의 왼쪽 포트에 놓거나 ‘여기가 뒤’를 누른다.")
                .font(.caption)
            Spacer()
            Button("취소") { linking = nil }
                .buttonStyle(.plain).font(.caption)
                .contentShape(Rectangle())
        }
        .padding(.horizontal, 20).padding(.bottom, 10)
    }

    // MARK: 판

    private var tracks: some View {
        ScrollView([.horizontal, .vertical]) {
            VStack(alignment: .leading, spacing: 0) {
                columnHeader
                ForEach(visibleTracks) { track in
                    Divider()
                    trackSection(track)
                }
            }
            .padding(20)
            // 선은 카드 뒤에 깔린다. 앞에 그으면 제목을 가린다.
            .backgroundPreferenceValue(NodeBoundsKey.self) { anchors in
                if showEdges { edgeLayer(anchors) }
            }
        }
    }

    private func edgeLayer(_ anchors: [String: Anchor<CGRect>]) -> some View {
        GeometryReader { proxy in
            ForEach(edges(), id: \.self) { edge in
                if let from = anchors[edge.from], let to = anchors[edge.to] {
                    EdgeShape(from: proxy[from], to: proxy[to])
                        .stroke(
                            edge.crossesProjects ? Color.accentColor
                                : (edge.satisfied ? Color.green : Color.orange),
                            style: StrokeStyle(
                                lineWidth: edge.crossesProjects ? 2.4 : 1.4,
                                lineCap: .round
                            )
                        )
                        .opacity(edge.satisfied ? 0.55 : 0.85)
                }
            }
        }
        .allowsHitTesting(false)
    }

    private var columnHeader: some View {
        HStack(alignment: .bottom, spacing: 12) {
            Text("프로젝트")
                .font(.caption.bold()).foregroundStyle(.secondary)
                .frame(width: Self.trackWidth, alignment: .leading)
            ForEach(Self.columns, id: \.status) { column in
                HStack(spacing: 6) {
                    Text(column.title).font(.caption.bold())
                    Text("\(count(of: column.status))")
                        .font(.caption2)
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .background(.quaternary.opacity(0.5), in: Capsule())
                }
                .foregroundStyle(.secondary)
                .frame(width: Self.columnWidth, alignment: .leading)
            }
        }
        .padding(.bottom, 10)
    }

    private func trackSection(_ track: BoardTrack) -> some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(track.projectName).font(.headline).lineLimit(2)
                Text(trackSubtitle(track)).font(.caption2).foregroundStyle(.secondary)
                Button("떼기") {
                    Task { _ = await model.disconnectTrack(projectID: track.projectID) }
                }
                .buttonStyle(.plain).font(.caption2)
                // hit-area: 글자만 눌리지 않게 도형을 준다.
                .contentShape(Rectangle())
                .foregroundStyle(.secondary)
                .padding(.top, 2)
            }
            .frame(width: Self.trackWidth, alignment: .leading)

            ForEach(Self.columns, id: \.status) { column in
                columnCell(track, status: column.status)
            }
        }
        .padding(.vertical, 12)
    }

    private func trackSubtitle(_ track: BoardTrack) -> String {
        let active = track.nodes.filter { $0.status == "active" }.count
        if active > 0 { return "\(active) 하는 중" }
        return track.nodes.isEmpty ? "티켓 없음" : "\(track.nodes.count) 티켓"
    }

    private func columnCell(_ track: BoardTrack, status: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(cards(in: track, status: status)) { node in
                nodeCard(node)
            }
            if status == "todo" { addNodeField(track) }
        }
        .frame(width: Self.columnWidth, alignment: .top)
        .frame(maxHeight: .infinity, alignment: .top)
        .dropDestination(for: String.self) { items, _ in
            guard let payload = items.first,
                  let nodeID = Self.moved(payload) else { return false }
            move(nodeID: nodeID, to: status)
            return true
        }
    }

    // MARK: 티켓

    private func nodeCard(_ node: BoardNode) -> some View {
        HStack(spacing: 0) {
            port(node, leading: true)
            nodeCardBody(node)
            port(node, leading: false)
        }
        .onHover { hoveredCardID = $0 ? node.id : (hoveredCardID == node.id ? nil : hoveredCardID) }
    }

    private func nodeCardBody(_ node: BoardNode) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            // 티켓 이름이 방과 번호를 같이 들고 다닌다. 사람이 대화에서 부를
            // 이름도 이것이고, 에이전트가 명령에 쓸 이름도 이것이다.
            if let name = ticketName(node) {
                Text(name)
                    .font(.system(size: 10, weight: .semibold, design: .monospaced))
                    .foregroundStyle(.secondary)
            }
            Text(node.title).font(.callout)
                .fixedSize(horizontal: false, vertical: true)

            ForEach(node.blockedBy, id: \.self) { blockerID in
                blockerChip(node, blockerID: blockerID)
            }

            if let source = linking, source.id != node.id {
                let refusal = BoardGraph.refusal(source: source, target: node, nodes: allNodes)
                HStack(spacing: 6) {
                    Button("여기가 뒤") { link(source: source, target: node) }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.small).font(.caption2)
                        .disabled(refusal != nil)
                    if let refusal {
                        Text(refusal).font(.caption2).foregroundStyle(.secondary)
                    }
                }
            } else {
                HStack(spacing: 8) {
                    Spacer()
                    Button(role: .destructive) {
                        Task { _ = await model.removeBoardNode(nodeID: node.id) }
                    } label: {
                        Image(systemName: "trash").font(.caption2)
                            .frame(width: 18, height: 18)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .opacity(hoveredCardID == node.id ? 1 : 0)
                }
            }
        }
        .padding(11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .anchorPreference(key: NodeBoundsKey.self, value: .bounds) { [node.id: $0] }
        .background(.background, in: RoundedRectangle(cornerRadius: 9))
        .overlay {
            RoundedRectangle(cornerRadius: 9)
                .stroke(cardStroke(node), lineWidth: linking?.id == node.id ? 2 : 1)
        }
        .shadow(color: .black.opacity(0.06), radius: 3, y: 1)
        .draggable("move:\(node.id)")
        // 카드가 열을 덮고 있어서 여기서 안 받으면 카드마다 죽은 구역이 된다.
        .dropDestination(for: String.self) { items, _ in
            guard let payload = items.first,
                  let movedID = Self.moved(payload), movedID != node.id else { return false }
            move(nodeID: movedID, to: node.status)
            return true
        }
    }

    /// ARCH-12. 서버가 낡아 번호나 프리픽스가 없으면 이름을 안 보여준다 —
    /// 반쪽짜리 이름을 보여주면 사람이 그것을 외운다.
    private func ticketName(_ node: BoardNode) -> String? {
        guard let number = node.number,
              let prefix = model.board.tracks
                  .first(where: { $0.projectID == node.projectID })?.ticketPrefix
        else { return nil }
        return "\(prefix)-\(number)"
    }

    private func cardStroke(_ node: BoardNode) -> Color {
        if linking?.id == node.id { return .green }
        if node.state == "waiting" { return .orange.opacity(0.5) }
        return .secondary.opacity(0.2)
    }

    /// 선행마다 한 칩이다. 프로젝트 이름으로 묶으면 둘을 기다릴 때 어느 것을
    /// 끊는지 고를 수 없다. 충족과 대기를 색으로 가르고 방을 넘는 것은 적는다.
    private func blockerChip(_ node: BoardNode, blockerID: String) -> some View {
        let blocker = allNodes.first { $0.id == blockerID }
        let satisfied = blocker?.status == "done"
        let crosses = blocker.map { $0.projectID != node.projectID } ?? false
        let tint: Color = satisfied ? .green : .orange
        return HStack(spacing: 4) {
            Image(systemName: satisfied ? "checkmark.circle.fill" : "clock")
                .font(.system(size: 9))
            Text(chipText(blocker: blocker, crosses: crosses, satisfied: satisfied))
                .font(.caption2)
                .fixedSize(horizontal: false, vertical: true)
            Button {
                Task { _ = await model.unlinkBoardNodes(nodeID: node.id, waitsFor: blockerID) }
            } label: {
                Image(systemName: "xmark").font(.system(size: 8, weight: .bold))
                    // hit-area: 8pt 글리프는 손으로 못 맞춘다.
                    .frame(width: 15, height: 15)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help("이 선을 끊는다")
        }
        .foregroundStyle(tint)
        .padding(.horizontal, 6).padding(.vertical, 2)
        .background(tint.opacity(0.12), in: Capsule())
        .overlay {
            if crosses { Capsule().stroke(Color.accentColor.opacity(0.5), lineWidth: 1) }
        }
    }

    private func chipText(blocker: BoardNode?, crosses: Bool, satisfied: Bool) -> String {
        // 이름이 있으면 이름으로 부른다. 제목은 두 방에서 겹칠 수 있다.
        let name = blocker.flatMap(ticketName) ?? blocker?.title ?? "다른 방의 일"
        return "\(name) \(satisfied ? "뒤 · 충족" : "대기")"
    }

    private func projectName(of node: BoardNode?) -> String {
        guard let node else { return "" }
        return model.board.tracks.first { $0.projectID == node.projectID }?.projectName ?? ""
    }

    // MARK: 포트
    //
    // 평소엔 흔적만 남기고, 카드에 올리면 커지고, 놓을 수 있을 때 가장 커진다.
    // 늘 크면 티켓을 읽는 데 방해가 되고, 늘 작으면 못 맞춘다.

    @ViewBuilder
    private func port(_ node: BoardNode, leading: Bool) -> some View {
        let armed = linking != nil && linking?.id != node.id
        let hovered = hoveredCardID == node.id
        let droppable = leading && armed
            && BoardGraph.refusal(source: linking!, target: node, nodes: allNodes) == nil
        let dot: CGFloat = droppable ? 13 : (hovered ? 9 : 5)

        if leading {
            Circle()
                .fill(droppable ? Color.green : Color.secondary.opacity(hovered ? 0.5 : 0.25))
                .frame(width: dot, height: dot)
                .frame(width: 22, height: 22)
                .contentShape(Rectangle())
                .onTapGesture {
                    if let source = linking, droppable { link(source: source, target: node) }
                }
                .help(droppable ? "여기 놓으면 뒤에 붙는다" : "선행이 들어오는 자리")
        } else {
            let isSource = linking?.id == node.id
            Button { linking = isSource ? nil : node } label: {
                Circle()
                    .fill(isSource ? Color.green : Color.accentColor.opacity(hovered ? 1 : 0.45))
                    .frame(width: isSource ? 13 : dot, height: isSource ? 13 : dot)
                    .frame(width: 22, height: 22)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(isSource ? "잡힘. 다시 누르면 놓는다" : "잡는다. 이 카드가 앞이 된다")
        }
    }

    private func addNodeField(_ track: BoardTrack) -> some View {
        HStack(spacing: 6) {
            Image(systemName: "plus").font(.caption2).foregroundStyle(.secondary)
            TextField(
                "티켓",
                text: Binding(
                    get: { draftTitles[track.projectID] ?? "" },
                    set: { draftTitles[track.projectID] = $0 }
                )
            )
            .textFieldStyle(.plain).font(.caption)
            .onSubmit { addNode(to: track) }
        }
        .padding(.horizontal, 10).padding(.vertical, 7)
        .overlay {
            RoundedRectangle(cornerRadius: 8)
                .strokeBorder(style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
                .foregroundStyle(.quaternary)
        }
        .padding(.leading, 22)
    }

    // MARK: 셈과 규칙

    private var allNodes: [BoardNode] { model.board.tracks.flatMap(\.nodes) }

    private var visibleTracks: [BoardTrack] {
        guard onlyLinked else { return model.board.tracks }
        let linked = Set(allNodes.flatMap { [$0.blockedBy, $0.blockedBy.isEmpty ? [] : [$0.id]].flatMap { $0 } })
        return model.board.tracks.filter { track in
            track.nodes.contains { linked.contains($0.id) }
        }
    }

    private func cards(in track: BoardTrack, status: String) -> [BoardNode] {
        let inColumn = track.nodes.filter { $0.status == status }
        guard onlyLinked else { return inColumn }
        let referenced = Set(allNodes.flatMap(\.blockedBy))
        return inColumn.filter { !$0.blockedBy.isEmpty || referenced.contains($0.id) }
    }

    private func count(of status: String) -> Int {
        allNodes.filter { $0.status == status }.count
    }

    private func edges() -> [BoardEdge] {
        let byID = Dictionary(uniqueKeysWithValues: allNodes.map { ($0.id, $0) })
        return allNodes.flatMap { node in
            node.blockedBy.compactMap { blockerID -> BoardEdge? in
                guard let blocker = byID[blockerID] else { return nil }
                return BoardEdge(
                    from: blockerID, to: node.id,
                    crossesProjects: blocker.projectID != node.projectID,
                    satisfied: blocker.status == "done"
                )
            }
        }
    }

    private func move(nodeID: String, to status: String) {
        guard let node = allNodes.first(where: { $0.id == nodeID }) else { return }
        if let blocker = BoardGraph.blocksStarting(node, to: status, nodes: allNodes) {
            model.errorMessage = "‘\(blocker.title)’가 아직 안 끝났다. 그것부터 끝낸다."
            return
        }
        Task { _ = await model.setBoardNodeStatus(nodeID: nodeID, status: status) }
    }

    private func link(source: BoardNode, target: BoardNode) {
        Task {
            _ = await model.linkBoardNodes(nodeID: target.id, waitsFor: source.id)
            linking = nil
        }
    }

    private static func moved(_ payload: String) -> String? {
        payload.hasPrefix("move:") ? String(payload.dropFirst(5)) : nil
    }

    private func draft(_ track: BoardTrack) -> String {
        (draftTitles[track.projectID] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func addNode(to track: BoardTrack) {
        let title = draft(track)
        guard !title.isEmpty else { return }
        Task {
            _ = await model.addBoardNode(projectID: track.projectID, title: title)
            draftTitles[track.projectID] = ""
        }
    }
}

/// 그릴 선 하나. 프로젝트를 넘는지와 선행이 끝났는지를 같이 들고 다닌다 —
/// 그리는 자리에서 다시 찾으면 카드마다 전체를 훑게 된다.
struct BoardEdge: Hashable {
    let from: String
    let to: String
    let crossesProjects: Bool
    let satisfied: Bool
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
