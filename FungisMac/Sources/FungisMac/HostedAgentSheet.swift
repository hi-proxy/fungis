import SwiftUI

struct HostedAgentSheet: View {
    let role: WorkspaceRole
    @ObservedObject var coordinator: HostedAgentCoordinator
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 5) {
                    Text("Start hosted agent").font(.title2.bold())
                    Text("@\(role.name)의 새 담당 세션을 앱에서 준비합니다.")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("닫기") { dismiss() }
            }

            ForEach(HostedAgentProviderID.allCases) { provider in
                providerCard(provider)
            }

            Label(
                "이번 단계는 host 실행과 구독 인증 확인까지입니다. 역할 배정과 메시지 전달은 다음 단계에서 연결합니다.",
                systemImage: "info.circle"
            )
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        .padding(24)
        .frame(width: 560)
    }

    @ViewBuilder
    private func providerCard(_ provider: HostedAgentProviderID) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Image(systemName: provider == .codex ? "terminal.fill" : "sparkles")
                    .frame(width: 24)
                VStack(alignment: .leading, spacing: 2) {
                    Text(provider.displayName).font(.headline)
                    Text(provider.detail).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if provider.isAvailable {
                    Text(coordinator.codexState.title)
                        .font(.caption.bold())
                        .foregroundStyle(statusColor)
                } else {
                    Text("COMING LATER")
                        .font(.caption2.bold()).foregroundStyle(.secondary)
                }
            }

            if provider == .codex {
                codexActions
                if case let .failed(message) = coordinator.codexState {
                    Text(message).font(.caption).foregroundStyle(.red)
                        .textSelection(.enabled)
                }
            }
        }
        .padding(16)
        .background(.quaternary.opacity(0.25), in: RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(provider.isAvailable ? Color.accentColor.opacity(0.25) : .clear)
        )
    }

    @ViewBuilder
    private var codexActions: some View {
        switch coordinator.codexState {
        case .stopped, .failed:
            Button("Start Codex app-server", systemImage: "play.fill") {
                Task { await coordinator.startCodex() }
            }
            .buttonStyle(.borderedProminent)
        case .starting:
            HStack { ProgressView().controlSize(.small); Text("Codex 확인 중…") }
        case let .ready(account):
            HStack {
                if account.authentication.isChatGPT {
                    Label("구독 인증 준비됨", systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                } else {
                    Button("Sign in with ChatGPT", systemImage: "person.crop.circle.badge.checkmark") {
                        Task { await coordinator.beginCodexLogin() }
                    }
                    .buttonStyle(.borderedProminent)
                }
                Spacer()
                Button("Refresh") { Task { await coordinator.refreshCodexAccount() } }
                Button("Stop") { Task { await coordinator.stopCodex() } }
            }
            if case let .chatGPT(_, email) = account.authentication, let email {
                Text(email).font(.caption2).foregroundStyle(.tertiary)
            } else if case .apiKey = account.authentication {
                Text("API key 대신 ChatGPT 로그인을 기본 경로로 사용합니다.")
                    .font(.caption2).foregroundStyle(.orange)
            }
        case .waitingForLogin:
            HStack {
                ProgressView().controlSize(.small)
                Text("브라우저에서 ChatGPT 로그인을 마치세요.")
                Spacer()
                Button("Cancel") { Task { await coordinator.stopCodex() } }
            }
        }
    }

    private var statusColor: Color {
        switch coordinator.codexState {
        case .ready: .green
        case .failed: .red
        case .waitingForLogin, .starting: .orange
        case .stopped: .secondary
        }
    }
}
