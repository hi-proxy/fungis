import SwiftUI
import UniformTypeIdentifiers

struct RolesView: View {
    @EnvironmentObject private var model: AppModel
    @State private var showCreate = false
    @State private var editingRole: WorkspaceRole?
    @State private var assigningRole: WorkspaceRole?
    @State private var historyRole: WorkspaceRole?
    @State private var deletingRole: WorkspaceRole?
    @State private var editingPM = false
    @State private var showConvene = false

    private var isHQ: Bool {
        model.snapshot.projects.first { $0.id == model.selectedProjectID }?.isHQ == true
    }

    private var convened: [BoardCandidate] {
        (model.board.candidates ?? []).filter(\.connected)
    }

    /// HQ에서는 이 자리가 소집된 방이다. lead를 카드에 적는다. 없으면 어디서
    /// 세우는지까지 적는다 — 목록만 보고 왜 못 부르는지 몰라서는 안 된다.
    private func convenedCard(_ candidate: BoardCandidate) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(candidate.name).font(.headline)
            if let lead = candidate.lead {
                Label("lead · \(lead.name)", systemImage: "star.fill")
                    .font(.caption).foregroundStyle(.secondary)
                if let agent = lead.agentName {
                    Text(agent).font(.caption2).foregroundStyle(.tertiary)
                }
            } else {
                Label("lead 없음", systemImage: "exclamationmark.triangle.fill")
                    .font(.caption).foregroundStyle(.orange)
                Text("소집에서 세운다").font(.caption2).foregroundStyle(.tertiary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.quaternary.opacity(0.25), in: RoundedRectangle(cornerRadius: 12))
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    // HQ의 구성원은 역할이 아니라 소집된 방이다. 자리는 같고
                    // 뜻만 바뀐다.
                    if isHQ {
                        Button("소집", systemImage: "person.2.badge.plus") {
                            showConvene = true
                        }
                    } else {
                        Button("Add role", systemImage: "plus") { showCreate = true }
                    }
                    Spacer()
                    Button("Initialize", systemImage: "sparkles") {
                        Task { await model.initializeChat() }
                    }
                    .disabled(model.isMutating || !model.snapshot.roles.contains(where: \.assigned))
                    .help("담당자가 배정된 모든 역할에게 Fungis 사용법을 보낸다")
                }

                LazyVGrid(columns: [GridItem(.adaptive(minimum: 260), spacing: 12)], spacing: 12) {
                    pmCard
                    if isHQ {
                        ForEach(convened) { candidate in convenedCard(candidate) }
                    } else {
                        ForEach(model.snapshot.roles) { role in roleCard(role) }
                    }
                }

                if isHQ && convened.isEmpty {
                    ContentUnavailableView(
                        "소집된 방이 없다", systemImage: "person.2",
                        description: Text("소집을 눌러 프로젝트를 부른다. 방마다 lead가 있어야 부를 수 있다.")
                    ).frame(maxWidth: .infinity, minHeight: 240)
                } else if !isHQ && model.snapshot.roles.isEmpty {
                    ContentUnavailableView(
                        "No roles", systemImage: "person.badge.key",
                        description: Text("front1처럼 계속 유지할 역할 주소를 만드세요.")
                    ).frame(maxWidth: .infinity, minHeight: 240)
                }
            }.padding(16)
        }
        .sheet(isPresented: $showConvene) { ConveneSheet() }
        .sheet(isPresented: $showCreate) {
            RoleEditor(role: nil) { name, prompt in
                if await model.createRole(name: name, onboardingPrompt: prompt) {
                    showCreate = false
                }
            }
        }
        .sheet(item: $editingRole) { role in
            RoleEditor(role: role) { name, prompt in
                if await model.updateRole(id: role.id, name: name, onboardingPrompt: prompt) {
                    editingRole = nil
                }
            }
        }
        .sheet(item: $assigningRole) { role in
            AssignmentEditor(
                role: role, agents: model.snapshot.agents,
                coordinator: model.hostedAgents, roles: model.snapshot.roles,
                projectWorkspacePath: model.snapshot.projectRepositories.first {
                    $0.projectID == model.selectedProjectID
                }?.path
            ) { surfaceID, onboarding in
                if await model.connectAndAssign(
                    roleID: role.id, surfaceID: surfaceID, sendOnboarding: onboarding
                ) {
                    assigningRole = nil
                }
            } assignHosted: { session, onboarding in
                if await model.assignHosted(
                    roleID: role.id, session: session, sendOnboarding: onboarding
                ) { assigningRole = nil }
            } createHosted: { provider, cwd, configuration, onboarding in
                if await model.createHostedAndAssign(
                    provider: provider, roleID: role.id, cwd: cwd,
                    sendOnboarding: onboarding, configuration: configuration
                ) { assigningRole = nil }
            } stopHosted: { session in
                await model.stopHosted(session)
            }
        }
        .sheet(item: $historyRole) { role in
            AssignmentHistoryView(role: role)
        }
        .sheet(isPresented: $editingPM) { PMProfileEditor() }
        .confirmationDialog(
            "Delete role ‘\(deletingRole?.name ?? "")’? Its message and assignment history remain stored.",
            isPresented: Binding(
                get: { deletingRole != nil },
                set: { if !$0 { deletingRole = nil } }
            ), titleVisibility: .visible
        ) {
            Button("Delete role", role: .destructive) {
                if let role = deletingRole { Task { await model.deleteRole(id: role.id) } }
                deletingRole = nil
            }
        }
    }

    private var pmCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                PMAvatar(profile: model.snapshot.pmProfile, size: 46)
                Text(model.snapshot.pmProfile.displayName).font(.title3.bold())
                Spacer()
                Text("PM · ALWAYS PRESENT").font(.caption2.bold()).foregroundStyle(.purple)
            }
            Text("이 프로젝트의 고정 운영자입니다. 모든 메시지를 보고 직접 개입할 수 있습니다.")
                .font(.caption).foregroundStyle(.secondary)
            Divider()
            HStack { Spacer(); Button("Edit profile") { editingPM = true } }
        }.padding(16)
            .background(Color.purple.opacity(0.07), in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.purple.opacity(0.2)))
    }

    private func roleCard(_ role: WorkspaceRole) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                RoleAvatar(role: role, size: 46)
                Circle().fill(role.assigned && role.sessionConnected ? Color.green : Color.orange).frame(width: 9, height: 9)
                Text(role.name).font(.title3.bold())
                Spacer()
                Text(role.assigned ? (role.sessionConnected ? "ASSIGNED" : "SESSION OFFLINE") : "UNASSIGNED")
                    .font(.caption2.bold()).foregroundStyle(role.assigned && role.sessionConnected ? .green : .orange)
            }
            if role.assigned {
                Label(role.agentName ?? role.agentID ?? "Unknown session", systemImage: "terminal")
                    .font(.callout)
                if let since = role.assignedAt {
                    Text("Assigned since \(since)").font(.caption).foregroundStyle(.secondary)
                }
            } else {
                Text("Messages to this role will wait until a session is assigned.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            HStack(spacing: 8) {
                Label(
                    role.onboardingPrompt.isEmpty ? "No onboarding prompt" : "Onboarding ready",
                    systemImage: role.onboardingPrompt.isEmpty ? "doc" : "doc.text.fill"
                ).font(.caption).foregroundStyle(.secondary)
                Spacer()
            }
            Divider()
            HStack {
                Button(role.assigned ? "Replace" : "Assign") { assigningRole = role }
                if role.assigned {
                    Button("Unassign") { Task { await model.unassignRole(id: role.id) } }
                }
                Spacer()
                Menu {
                    Button("History") { historyRole = role }
                    Button("Edit") { editingRole = role }
                    Button("Delete", role: .destructive) { deletingRole = role }
                } label: {
                    Image(systemName: "ellipsis")
                }
                .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
            }
        }.padding(16)
            .background(.quaternary.opacity(0.25), in: RoundedRectangle(cornerRadius: 12))
    }
}

private struct PMProfileEditor: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var importingAvatar = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("PM profile").font(.title2.bold())
            HStack(spacing: 14) {
                PMAvatar(profile: model.snapshot.pmProfile, size: 64)
                Button(model.snapshot.pmProfile.hasAvatar ? "Replace photo" : "Choose photo") {
                    importingAvatar = true
                }
                if model.snapshot.pmProfile.hasAvatar {
                    Button("Remove", role: .destructive) {
                        Task { _ = await model.deletePMAvatar() }
                    }
                }
            }
            TextField("Display name", text: $name)
            HStack { Spacer(); Button("Save") {
                Task { if await model.updatePMProfile(displayName: name) { dismiss() } }
            }.buttonStyle(.borderedProminent).disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) }
        }.padding(24).frame(width: 460)
            .onAppear { name = model.snapshot.pmProfile.displayName }
            .fileImporter(
                isPresented: $importingAvatar,
                allowedContentTypes: [.png, .jpeg, .gif], allowsMultipleSelection: false
            ) { result in
                guard case let .success(urls) = result, let url = urls.first else { return }
                let accessing = url.startAccessingSecurityScopedResource()
                defer { if accessing { url.stopAccessingSecurityScopedResource() } }
                do {
                    let data = try Data(contentsOf: url)
                    let mediaType = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType
                        ?? "application/octet-stream"
                    Task { _ = await model.putPMAvatar(data: data, mediaType: mediaType) }
                } catch { model.errorMessage = error.localizedDescription }
            }
    }
}

private struct RoleEditor: View {
    @EnvironmentObject private var model: AppModel
    let role: WorkspaceRole?
    let save: (String, String) async -> Void
    @State private var name: String
    @State private var prompt: String
    @State private var importingAvatar = false

    init(role: WorkspaceRole?, save: @escaping (String, String) async -> Void) {
        self.role = role
        self.save = save
        _name = State(initialValue: role?.name ?? "")
        _prompt = State(initialValue: role?.onboardingPrompt ?? "")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(role == nil ? "New role" : "Edit role").font(.title2.bold())
            if let role {
                HStack(spacing: 14) {
                    RoleAvatar(role: role, size: 64)
                    VStack(alignment: .leading, spacing: 7) {
                        Button(role.hasAvatar ? "Replace photo" : "Choose photo") {
                            importingAvatar = true
                        }
                        if role.hasAvatar {
                            Button("Remove photo", role: .destructive) {
                                Task { _ = await model.deleteRoleAvatar(id: role.id) }
                            }
                        }
                        Text("PNG, JPEG, or GIF · up to 2 MB")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                }
            } else {
                Text("Save the role first, then add an optional profile photo.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            TextField("Role address, e.g. front1", text: $name)
            Text("One-time onboarding prompt").font(.headline)
            TextEditor(text: $prompt).frame(height: 220).padding(6)
                .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 8))
            Text("The exact text is sent only when you enable onboarding during a new assignment.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Save") { Task { await save(name.trimmingCharacters(in: .whitespacesAndNewlines), prompt) } }
                    .buttonStyle(.borderedProminent)
                    .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }.padding(24).frame(width: 520)
            .fileImporter(
                isPresented: $importingAvatar,
                allowedContentTypes: [.png, .jpeg, .gif], allowsMultipleSelection: false
            ) { result in
                guard let role, case let .success(urls) = result,
                      let url = urls.first else { return }
                let accessing = url.startAccessingSecurityScopedResource()
                defer { if accessing { url.stopAccessingSecurityScopedResource() } }
                do {
                    let data = try Data(contentsOf: url)
                    let mediaType = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType
                        ?? "application/octet-stream"
                    Task { _ = await model.putRoleAvatar(id: role.id, data: data, mediaType: mediaType) }
                } catch {
                    model.errorMessage = error.localizedDescription
                }
            }
    }
}

private struct AssignmentEditor: View {
    let role: WorkspaceRole
    let agents: [AgentTerminal]
    @ObservedObject var coordinator: HostedAgentCoordinator
    let roles: [WorkspaceRole]
    let projectWorkspacePath: String?
    let assign: (String, Bool) async -> Void
    let assignHosted: (HostedAgentSession, Bool) async -> Void
    let createHosted: (
        HostedAgentProviderID, String, HostedAgentConfiguration, Bool
    ) async -> Void
    let stopHosted: (HostedAgentSession) async -> Void
    @State private var surfaceID: String
    @State private var hostedPrincipalID: String
    @State private var newProvider: HostedAgentProviderID?
    @State private var sendOnboarding: Bool
    @State private var confirmReassignment = false
    @State private var stoppingSession: HostedAgentSession?
    @State private var stoppingRecoveryPrincipalID: String?
    @State private var creationTask: Task<Void, Never>?
    @State private var workspacePath: String
    @State private var choosingWorkspace = false
    @State private var availableModels: [HostedModelOption] = []
    @State private var selectedModelID = ""
    @State private var selectedReasoningEffort = ""
    @State private var loadingModels = false
    @State private var modelError: String?

    init(
        role: WorkspaceRole, agents: [AgentTerminal],
        coordinator: HostedAgentCoordinator, roles: [WorkspaceRole],
        projectWorkspacePath: String?,
        assign: @escaping (String, Bool) async -> Void,
        assignHosted: @escaping (HostedAgentSession, Bool) async -> Void,
        createHosted: @escaping (
            HostedAgentProviderID, String, HostedAgentConfiguration, Bool
        ) async -> Void,
        stopHosted: @escaping (HostedAgentSession) async -> Void
    ) {
        self.role = role
        self.agents = agents
        self.coordinator = coordinator
        self.roles = roles
        self.projectWorkspacePath = projectWorkspacePath
        self.assign = assign
        self.assignHosted = assignHosted
        self.createHosted = createHosted
        self.stopHosted = stopHosted
        let current = agents.first { $0.principalID == role.agentID }
        let currentHosted = coordinator.sessions.first {
            $0.projectID == role.workspaceID && $0.principalID == role.agentID
        }
        let usable = agents.first { $0.connected || $0.bindingVerified }
        _surfaceID = State(
            initialValue: currentHosted == nil ? current?.surfaceID ?? usable?.surfaceID ?? "" : ""
        )
        _hostedPrincipalID = State(initialValue: currentHosted?.principalID ?? "")
        _newProvider = State(initialValue: nil)
        _workspacePath = State(initialValue: projectWorkspacePath ?? "")
        // 역할 설명이 비어 있다고 꺼두면 배정 init이 통째로 안 나간다. 그러면
        // 에이전트는 자기가 배정된 줄도 모르고 PM은 보냈다고 믿는다. 설명 유무는
        // 덧붙일 문구가 있느냐일 뿐, 부를지 말지가 아니다.
        _sendOnboarding = State(initialValue: true)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Assign \(role.name)").font(.title2.bold())
            Text("연결되지 않은 세션을 골라도 된다. 배정하면서 함께 연결한다.")
                .font(.caption).foregroundStyle(.secondary)
            sessionList
            if let occupied = selectedExistingRole, occupied.id != role.id {
                Label(
                    "This will remove \(selectedName) from \(occupied.name) and leave that role unassigned.",
                    systemImage: "exclamationmark.triangle.fill"
                ).font(.caption).foregroundStyle(.orange)
            }
            Toggle("Send the init call once", isOn: $sendOnboarding)
            if !role.onboardingPrompt.isEmpty {
                Text(role.onboardingPrompt).font(.caption).foregroundStyle(.secondary)
                    .lineLimit(5).padding(10)
                    .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))
            }
            HStack { Spacer(); assignmentButton }
        }.padding(24).frame(width: 520)
            .confirmationDialog(
                "Move \(selectedName) from ‘\(selectedExistingRole?.name ?? "")’ to ‘\(role.name)’?",
                isPresented: $confirmReassignment, titleVisibility: .visible
            ) {
                Button("Move session", role: .destructive) {
                    performAssignment()
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("The previous role becomes unassigned. Its history and messages remain intact.")
            }
            .confirmationDialog(
                "Stop ‘\(stoppingSession?.localName ?? "")’?",
                isPresented: Binding(
                    get: { stoppingSession != nil },
                    set: { if !$0 { stoppingSession = nil } }
                ), titleVisibility: .visible
            ) {
                Button("Stop session", role: .destructive) {
                    guard let session = stoppingSession else { return }
                    stoppingSession = nil
                    if hostedPrincipalID == session.principalID { hostedPrincipalID = "" }
                    Task { await stopHosted(session) }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("The provider process stops. Any role assignment remains as SESSION OFFLINE.")
            }
            .confirmationDialog(
                replacingFailedRecovery ? "Replace failed hosted session?"
                    : "Stop failed hosted session?",
                isPresented: Binding(
                    get: { stoppingRecoveryPrincipalID != nil },
                    set: { if !$0 { stoppingRecoveryPrincipalID = nil } }
                ), titleVisibility: .visible
            ) {
                Button(replacingFailedRecovery ? "Replace session" : "Stop session", role: .destructive) {
                    guard let principalID = stoppingRecoveryPrincipalID else { return }
                    let replace = role.agentID == principalID
                    stoppingRecoveryPrincipalID = nil
                    Task {
                        await coordinator.stopFailedRecovery(principalID)
                        if replace {
                            hostedPrincipalID = ""
                            surfaceID = ""
                            newProvider = .codex
                            if validWorkspacePath == nil { choosingWorkspace = true }
                        }
                    }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text(replacingFailedRecovery
                     ? "The broken recovery record is removed. Choose a model, then Create & Assign replaces this role with a new Codex session."
                     : "The recovery record is removed. Any role assignment remains offline.")
            }
            .fileImporter(
                isPresented: $choosingWorkspace,
                allowedContentTypes: [.folder], allowsMultipleSelection: false
            ) { result in
                guard case let .success(urls) = result, let url = urls.first else { return }
                let accessing = url.startAccessingSecurityScopedResource()
                workspacePath = url.path
                if accessing { url.stopAccessingSecurityScopedResource() }
            }
            .onDisappear { creationTask?.cancel() }
            .task(id: newProvider) {
                guard let provider = newProvider else {
                    availableModels = []
                    selectedModelID = ""
                    selectedReasoningEffort = ""
                    modelError = nil
                    return
                }
                await loadModels(provider)
            }
    }

    private var sessionList: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
            Text("Terminal sessions").font(.caption.bold()).foregroundStyle(.secondary)
                .padding(.horizontal, 10).padding(.top, 10)
            ForEach(Array(agents.enumerated()), id: \.element.id) { index, agent in
                sessionRow(agent)
                if index < agents.count - 1 { Divider() }
            }
            Divider().padding(.vertical, 6)
            Text("Hosted sessions").font(.caption.bold()).foregroundStyle(.secondary)
                .padding(.horizontal, 10)
            if hostedSessions.isEmpty {
                Text("생성된 hosted session이 없습니다.")
                    .font(.caption).foregroundStyle(.secondary).padding(10)
            } else {
                ForEach(hostedSessions) { session in hostedSessionRow(session) }
            }
            ForEach(coordinator.recoveryFailures.keys.sorted(), id: \.self) { principalID in
                HStack(spacing: 8) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("복구 실패 · \(String(principalID.suffix(8)))")
                            .font(.caption.bold()).foregroundStyle(.red)
                        Text(coordinator.recoveryFailures[principalID] ?? "알 수 없는 오류")
                            .font(.caption2).foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                    Spacer()
                    Button(role.agentID == principalID ? "Replace…" : "Stop", role: .destructive) {
                        stoppingRecoveryPrincipalID = principalID
                    }
                    .buttonStyle(.borderless)
                }
                .padding(10)
            }
            Divider().padding(.vertical, 6)
            Text("Create hosted session")
                .font(.caption.bold()).foregroundStyle(.secondary).padding(.horizontal, 10)
            ForEach(HostedAgentProviderID.allCases) { provider in
                newHostedSessionRow(provider)
            }
            if newProvider != nil {
                workspaceSelection
                modelSelection
            }
            switch coordinator.creationState {
            case .starting:
                Label("app-server 시작 중…", systemImage: "hourglass")
                    .font(.caption).foregroundStyle(.secondary).padding(10)
            case .waitingForLogin:
                HStack {
                    Label(
                        "브라우저에서 ChatGPT 로그인을 마치세요.",
                        systemImage: "person.crop.circle"
                    )
                    .font(.caption).foregroundStyle(.orange)
                    Spacer()
                    Button("Cancel") { creationTask?.cancel() }
                }.padding(10)
            case let .failed(message):
                Text(message).font(.caption).foregroundStyle(.red).padding(10)
                    .textSelection(.enabled)
            case .stopped, .ready:
                EmptyView()
            }
            }
        }
        .frame(maxHeight: 460)
        .background(.quaternary.opacity(0.25), in: RoundedRectangle(cornerRadius: 9))
    }

    private func sessionRow(_ agent: AgentTerminal) -> some View {
        let currentRole = assignedRole(for: agent)
        // binding이 유일하게 검증되지 않은 세션에는 붙지 않는다.
        let selectable = agent.connected || agent.bindingVerified
        return Button {
            surfaceID = agent.surfaceID
            hostedPrincipalID = ""
            newProvider = nil
        } label: {
            HStack(spacing: 10) {
                Image(systemName: surfaceID == agent.surfaceID ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(
                        surfaceID == agent.surfaceID ? Color.accentColor : Color.secondary
                    )
                VStack(alignment: .leading, spacing: 3) {
                    Text(displayName(agent)).foregroundStyle(.primary).lineLimit(1)
                    HStack(spacing: 5) {
                        Circle().fill(statusTint(agent, currentRole)).frame(width: 6, height: 6)
                        Text(statusLabel(agent, currentRole)).font(.caption)
                            .foregroundStyle(statusTint(agent, currentRole))
                    }
                }
                Spacer()
                Text(agent.provider.uppercased())
                    .font(.caption2.bold()).foregroundStyle(.secondary)
            }.padding(10).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!selectable)
        .opacity(selectable ? 1 : 0.45)
    }

    private func hostedSessionRow(_ session: HostedAgentSession) -> some View {
        HStack(spacing: 0) {
            Button {
                hostedPrincipalID = session.principalID
                surfaceID = ""
                newProvider = nil
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: hostedPrincipalID == session.principalID
                          ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(hostedPrincipalID == session.principalID
                                         ? Color.accentColor : Color.secondary)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(session.localName).foregroundStyle(.primary)
                        Text(hostedStatusLabel(session)).font(.caption)
                            .foregroundStyle(hostedAssignedRole(session) == nil ? .green : .orange)
                        if let model = session.model, let effort = session.reasoningEffort {
                            Text("\(model) · \(effort)")
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                        if let progress = coordinator.activeTurns[session.id] {
                            Text(progress.text.isEmpty ? "Codex 작업 중…" : progress.text)
                                .font(.caption2).foregroundStyle(.secondary).lineLimit(3)
                            if let activity = progress.activities.last {
                                Label(
                                    activity.detail.map { "\(activity.title) · \($0)" }
                                        ?? activity.title,
                                    systemImage: activity.state == .running
                                        ? "hourglass" : activity.state == .succeeded
                                        ? "checkmark.circle.fill" : "xmark.circle.fill"
                                )
                                .font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                            }
                            if let interruptError = progress.interruptError {
                                Text(interruptError).font(.caption2)
                                    .foregroundStyle(.red).lineLimit(2)
                            }
                        } else if let failure = coordinator.turnFailures[session.id] {
                            Text(failure).font(.caption2).foregroundStyle(.red).lineLimit(3)
                        }
                    }
                    Spacer()
                    Text(session.provider.displayName.uppercased())
                        .font(.caption2.bold()).foregroundStyle(.secondary)
                }.padding(10).contentShape(Rectangle())
            }.buttonStyle(.plain)
            if let progress = coordinator.activeTurns[session.id] {
                Button(progress.interruptRequested ? "중단 중…" : "Interrupt") {
                    Task { await coordinator.interruptTurn(session) }
                }
                .buttonStyle(.borderless)
                .disabled(progress.turnID == nil || progress.interruptRequested)
                .padding(.trailing, 8)
            } else if coordinator.turnFailures[session.id] != nil {
                Button("Retry") { coordinator.retryTurn(session) }
                    .buttonStyle(.borderless).padding(.trailing, 8)
            }
            Button("Stop", role: .destructive) { stoppingSession = session }
                .buttonStyle(.borderless).padding(.trailing, 10)
        }
    }

    private func newHostedSessionRow(_ provider: HostedAgentProviderID) -> some View {
        Button {
            guard provider.isAvailable else { return }
            newProvider = provider
            hostedPrincipalID = ""
            surfaceID = ""
            if validWorkspacePath == nil { choosingWorkspace = true }
        } label: {
            HStack(spacing: 10) {
                Image(systemName: newProvider == provider
                      ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(newProvider == provider
                                     ? Color.accentColor : Color.secondary)
                VStack(alignment: .leading, spacing: 3) {
                    Text("New \(provider.displayName) session").foregroundStyle(.primary)
                    Text(provider.isAvailable ? "Create when confirmed" : "Coming later")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Text(provider.isAvailable ? "NEW" : "COMING LATER")
                    .font(.caption2.bold()).foregroundStyle(.secondary)
            }.padding(10).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!provider.isAvailable || creationInProgress)
        .opacity(provider.isAvailable ? 1 : 0.45)
    }

    private var workspaceSelection: some View {
        HStack(spacing: 10) {
            Image(systemName: validWorkspacePath == nil
                  ? "folder.badge.questionmark" : "folder.fill")
                .foregroundStyle(validWorkspacePath == nil ? .orange : .secondary)
            VStack(alignment: .leading, spacing: 3) {
                Text(validWorkspacePath ?? "Workspace 폴더를 선택하세요")
                    .font(.caption).lineLimit(2).textSelection(.enabled)
                if projectWorkspacePath == nil {
                    Text("이 프로젝트에는 지정된 workspace가 없습니다.")
                        .font(.caption2).foregroundStyle(.secondary)
                } else if validWorkspacePath == nil {
                    Text("지정된 workspace 경로를 사용할 수 없습니다.")
                        .font(.caption2).foregroundStyle(.orange)
                }
            }
            Spacer()
            Button("Choose…") { choosingWorkspace = true }
        }
        .padding(10)
    }

    @ViewBuilder
    private var modelSelection: some View {
        if loadingModels {
            Label("Codex 모델을 불러오는 중…", systemImage: "hourglass")
                .font(.caption).foregroundStyle(.secondary).padding(10)
        } else if let modelError {
            VStack(alignment: .leading, spacing: 6) {
                Text(modelError).font(.caption).foregroundStyle(.red).textSelection(.enabled)
                Button("Retry") {
                    guard let provider = newProvider else { return }
                    Task { await loadModels(provider) }
                }
            }.padding(10)
        } else if !availableModels.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Picker("Model", selection: modelSelectionBinding) {
                    ForEach(availableModels) { model in
                        Text(model.displayName).tag(model.id)
                    }
                }
                if let selectedModel {
                    Text(selectedModel.description)
                        .font(.caption2).foregroundStyle(.secondary)
                    Picker("Reasoning", selection: $selectedReasoningEffort) {
                        ForEach(selectedModel.supportedReasoningEfforts) { option in
                            Text(option.effort.capitalized).tag(option.effort)
                        }
                    }
                    if let option = selectedModel.supportedReasoningEfforts.first(
                        where: { $0.effort == selectedReasoningEffort }
                    ) {
                        Text(option.description).font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }.padding(10)
        }
    }

    private func displayName(_ agent: AgentTerminal) -> String {
        agent.nickname?.isEmpty == false ? agent.nickname! : agent.title
    }

    private func statusTint(_ agent: AgentTerminal, _ currentRole: WorkspaceRole?) -> Color {
        if !agent.connected && !agent.bindingVerified { return .secondary }
        if currentRole != nil { return .orange }
        return agent.connected ? .green : .blue
    }

    private func statusLabel(_ agent: AgentTerminal, _ currentRole: WorkspaceRole?) -> String {
        if !agent.connected && !agent.bindingVerified {
            return "binding 미검증 · 배정할 수 없음"
        }
        if let currentRole {
            return currentRole.id == role.id
                ? "Currently assigned to this role"
                : "Assigned to \(currentRole.name)"
        }
        return agent.connected ? "Available · not assigned" : "미연결 · 배정하며 연결한다"
    }

    private var assignmentButton: some View {
        Button(assignmentButtonLabel) {
            if let occupied = selectedExistingRole, occupied.id != role.id {
                confirmReassignment = true
            } else {
                performAssignment()
            }
        }.buttonStyle(.borderedProminent)
            .disabled(
                (surfaceID.isEmpty && hostedPrincipalID.isEmpty && newProvider == nil)
                    || (newProvider != nil && validWorkspacePath == nil)
                    || (newProvider != nil && selectedConfiguration == nil)
                    || creationInProgress
            )
    }

    private var assignmentButtonLabel: String {
        if newProvider != nil { return "Create & Assign" }
        if selectedHostedSession != nil { return "Assign" }
        if selectedExistingRole?.id == role.id { return "Keep assignment" }
        if let selected = selectedAgent, !selected.connected { return "Connect and assign" }
        return selectedExistingRole == nil ? "Assign" : "Reassign"
    }

    private func assignedRole(for agent: AgentTerminal) -> WorkspaceRole? {
        guard let principal = agent.principalID else { return nil }
        return roles.first { $0.agentID == principal }
    }

    private var selectedAgent: AgentTerminal? {
        agents.first { $0.surfaceID == surfaceID }
    }
    private var selectedHostedSession: HostedAgentSession? {
        hostedSessions.first { $0.principalID == hostedPrincipalID }
    }
    private var selectedExistingRole: WorkspaceRole? {
        if let session = selectedHostedSession {
            return roles.first { $0.agentID == session.principalID }
        }
        return selectedAgent.flatMap { assignedRole(for: $0) }
    }
    private var selectedName: String {
        if let newProvider { return "new \(newProvider.displayName) session" }
        return selectedHostedSession?.localName ?? selectedAgent.map(displayName) ?? "session"
    }

    private func performAssignment() {
        if let newProvider {
            guard let validWorkspacePath else { choosingWorkspace = true; return }
            guard let selectedConfiguration else { return }
            creationTask = Task {
                await createHosted(
                    newProvider, validWorkspacePath, selectedConfiguration, sendOnboarding
                )
            }
        } else if let session = selectedHostedSession {
            Task { await assignHosted(session, sendOnboarding) }
        } else {
            Task { await assign(surfaceID, sendOnboarding) }
        }
    }

    private var validWorkspacePath: String? {
        HostedWorkspaceDirectory.validatedPath(workspacePath)
    }

    private var selectedModel: HostedModelOption? {
        availableModels.first { $0.id == selectedModelID }
    }

    private var selectedConfiguration: HostedAgentConfiguration? {
        guard let selectedModel,
              selectedModel.supportedReasoningEfforts.contains(
                where: { $0.effort == selectedReasoningEffort }
              )
        else { return nil }
        return HostedAgentConfiguration(
            model: selectedModel.id, reasoningEffort: selectedReasoningEffort
        )
    }

    private var modelSelectionBinding: Binding<String> {
        Binding(
            get: { selectedModelID },
            set: { value in
                selectedModelID = value
                selectedReasoningEffort = availableModels.first {
                    $0.id == value
                }?.defaultReasoningEffort ?? ""
            }
        )
    }

    @MainActor
    private func loadModels(_ provider: HostedAgentProviderID) async {
        loadingModels = true
        modelError = nil
        availableModels = []
        selectedModelID = ""
        selectedReasoningEffort = ""
        defer { loadingModels = false }
        do {
            let models = try await coordinator.availableModels(provider: provider)
            guard !Task.isCancelled, newProvider == provider else { return }
            availableModels = models
            let initial = models.first(where: \.isDefault) ?? models[0]
            selectedModelID = initial.id
            selectedReasoningEffort = initial.defaultReasoningEffort
        } catch is CancellationError {
            return
        } catch {
            guard !Task.isCancelled else { return }
            modelError = error.localizedDescription
        }
    }

    private func hostedAssignedRole(_ session: HostedAgentSession) -> WorkspaceRole? {
        roles.first { $0.agentID == session.principalID }
    }

    private func hostedStatusLabel(_ session: HostedAgentSession) -> String {
        guard let assigned = hostedAssignedRole(session) else { return "Ready · not assigned" }
        return assigned.id == role.id
            ? "Currently assigned to this role"
            : "Assigned to \(assigned.name)"
    }

    private var creationInProgress: Bool {
        switch coordinator.creationState {
        case .starting, .waitingForLogin: true
        case .stopped, .ready, .failed: false
        }
    }

    private var replacingFailedRecovery: Bool {
        guard let principalID = stoppingRecoveryPrincipalID else { return false }
        return role.agentID == principalID
    }

    private var hostedSessions: [HostedAgentSession] {
        coordinator.sessions.filter { $0.projectID == role.workspaceID }
    }
}

private struct AssignmentHistoryView: View {
    @EnvironmentObject private var model: AppModel
    let role: WorkspaceRole
    @State private var items: [RoleAssignment] = []

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("\(role.name) assignment history").font(.title2.bold())
            if items.isEmpty {
                Text("No assignment history.").foregroundStyle(.secondary)
            } else {
                List(items) { item in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(item.agentName).font(.headline)
                            Spacer()
                            Text(item.endedAt == nil ? "ACTIVE" : "ENDED")
                                .font(.caption2.bold()).foregroundStyle(item.endedAt == nil ? .green : .secondary)
                        }
                        Text("\(item.assignedAt) → \(item.endedAt ?? "now")")
                            .font(.caption).foregroundStyle(.secondary)
                        if item.onboardingSent {
                            Label("Onboarding sent", systemImage: "paperplane.fill").font(.caption)
                        }
                    }.padding(.vertical, 4)
                }
            }
        }.padding(24).frame(width: 560, height: 440)
            .task { items = await model.roleHistory(id: role.id) }
    }
}
