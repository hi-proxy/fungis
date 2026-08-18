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

    private var tracks: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                ForEach(model.board.tracks) { track in
                    trackSection(track)
                }
            }
            .padding(20)
        }
    }

    private func trackSection(_ track: BoardTrack) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(track.projectName).font(.headline)
                Spacer()
                Button("보드에서 떼기") {
                    Task { _ = await model.disconnectTrack(projectID: track.projectID) }
                }
                .buttonStyle(.plain)
                .font(.caption)
                // hit-area: 글자만 눌리지 않게 도형을 준다.
                .contentShape(Rectangle())
                .foregroundStyle(.secondary)
            }
            ForEach(track.nodes) { node in
                nodeRow(node, in: track)
            }
            HStack {
                TextField(
                    "진행 중인 것을 올린다",
                    text: Binding(
                        get: { draftTitles[track.projectID] ?? "" },
                        set: { draftTitles[track.projectID] = $0 }
                    )
                )
                .onSubmit { addNode(to: track) }
                Button("올리기") { addNode(to: track) }
                    .disabled(draft(track).isEmpty)
            }
        }
    }

    private func nodeRow(_ node: BoardNode, in track: BoardTrack) -> some View {
        HStack(spacing: 10) {
            Picker("", selection: Binding(
                get: { node.status },
                set: { status in
                    Task { _ = await model.setBoardNodeStatus(nodeID: node.id, status: status) }
                }
            )) {
                Text("안 시작").tag("todo")
                Text("하는 중").tag("active")
                Text("끝").tag("done")
            }
            .labelsHidden()
            .frame(width: 110)

            Text(node.title)
            if node.state == "waiting" {
                Text("대기 · \(BoardSummary.blockerNames(of: node, in: model.board.tracks).joined(separator: ","))")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
            Spacer()
            Button(linking?.id == node.id ? "잇기 취소" : "잇기") {
                linking = linking?.id == node.id ? nil : node
            }
            .buttonStyle(.plain)
            .font(.caption)
            .contentShape(Rectangle())
            Button(role: .destructive) {
                Task { _ = await model.removeBoardNode(nodeID: node.id) }
            } label: {
                Image(systemName: "trash").font(.caption)
            }
            .buttonStyle(.plain)
            .contentShape(Rectangle())
        }
        .padding(.vertical, 3)
        .overlay(alignment: .leading) { linkTargetHint(for: node) }
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
