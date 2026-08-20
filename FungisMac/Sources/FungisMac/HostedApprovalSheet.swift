import SwiftUI

struct HostedApprovalPresenter: View {
    @ObservedObject var coordinator: HostedAgentCoordinator

    var body: some View {
        Color.clear
            .frame(width: 0, height: 0)
            .sheet(item: presented) { approval in
                HostedApprovalSheet(coordinator: coordinator, approval: approval)
                    .interactiveDismissDisabled()
            }
    }

    private var presented: Binding<HostedApprovalRequest?> {
        Binding(
            get: { coordinator.presentedApproval },
            set: { value in
                if value == nil { coordinator.postponePresentedApproval() }
            }
        )
    }
}

struct HostedApprovalBadge: View {
    @ObservedObject var coordinator: HostedAgentCoordinator

    var body: some View {
        if !coordinator.pendingApprovals.isEmpty {
            Button {
                coordinator.showNextApproval()
            } label: {
                HStack(spacing: 7) {
                    Image(systemName: "exclamationmark.shield.fill")
                        .foregroundStyle(.orange)
                    Text("권한 요청 \(coordinator.pendingApprovals.count)개")
                        .font(.caption).foregroundStyle(.orange)
                    Spacer()
                    Image(systemName: "chevron.up")
                        .font(.caption2).foregroundStyle(.tertiary)
                }
                .contentShape(Rectangle())
                .padding(.horizontal, 14).padding(.vertical, 8)
            }
            .buttonStyle(.plain)
            .help("대기 중인 hosted agent 권한 요청 열기")
        }
    }
}

private struct HostedApprovalSheet: View {
    @ObservedObject var coordinator: HostedAgentCoordinator
    let approval: HostedApprovalRequest

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .firstTextBaseline) {
                Label(approval.kind.title, systemImage: "exclamationmark.shield.fill")
                    .font(.title2.bold()).foregroundStyle(.orange)
                Spacer()
                Text(queuePosition).font(.caption).foregroundStyle(.secondary)
            }

            Text("Codex가 workspace 경계 밖의 작업을 요청했습니다.")
                .foregroundStyle(.secondary)

            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    detail("세션", approval.sessionLabel)
                    if let reason = approval.reason { detail("이유", reason) }
                    if let command = approval.command { detail("명령", command, monospaced: true) }
                    if let cwd = approval.cwd { detail("작업 폴더", cwd, monospaced: true) }
                    if let path = approval.path { detail("경로", path, monospaced: true) }
                    if let network = approval.network { detail("네트워크", network, monospaced: true) }
                    DisclosureGroup("원본 요청") {
                        Text(approval.detailJSON)
                            .font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled).padding(.top, 6)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }

            HStack {
                Text("자동 거절")
                Picker("자동 거절", selection: $coordinator.approvalTimeoutMinutes) {
                    Text("기한 없음").tag(0)
                    Text("5분").tag(5)
                    Text("15분").tag(15)
                    Text("30분").tag(30)
                }
                .labelsHidden().frame(width: 120)
                Spacer()
                Button("나중에") { coordinator.postponePresentedApproval() }
                Button("거절", role: .destructive) {
                    coordinator.resolve(approval, as: .deny)
                }
                Button("세션 동안 허용") {
                    coordinator.resolve(approval, as: .allowSession)
                }
                Button("이번만 허용") {
                    coordinator.resolve(approval, as: .allowOnce)
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(24)
        .frame(minWidth: 650, idealWidth: 720, minHeight: 500)
    }

    private var queuePosition: String {
        let sessionQueue = coordinator.pendingApprovals.filter {
            $0.principalID == approval.principalID
        }
        let index = sessionQueue.firstIndex { $0.id == approval.id } ?? 0
        return "\(index + 1) / \(sessionQueue.count)"
    }

    @ViewBuilder
    private func detail(_ label: String, _ value: String, monospaced: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.caption.bold()).foregroundStyle(.secondary)
            Text(value)
                .font(monospaced ? .system(.body, design: .monospaced) : .body)
                .textSelection(.enabled)
        }
    }
}
