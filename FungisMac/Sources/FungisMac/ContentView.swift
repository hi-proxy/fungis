import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @EnvironmentObject private var model: AppModel
    @State private var search = ""
    @State private var showAgents = false
    @State private var creatingProject = false
    @State private var renamingProject: FungisProject?
    @State private var projectName = ""
    @State private var repositoryProject: FungisProject?
    @State private var choosingRepository = false
    @State private var archivingProject: FungisProject?

    var body: some View {
        NavigationSplitView {
            ScrollView {
                LazyVStack(spacing: 2) {
                    // HQ는 방 하나가 아니라 방들을 보는 자리다. 정렬로 위에
                    // 올리기만 하면 스크롤하는 순간 그냥 첫 번째 방이 된다.
                    // 구획으로 갈라야 위계가 남는다.
                    ForEach(filteredProjects.filter(\.isHQ)) { project in
                        projectButton(project)
                    }
                    if !filteredProjects.filter(\.isHQ).isEmpty,
                       !filteredProjects.filter({ !$0.isHQ }).isEmpty {
                        Divider().padding(.vertical, 6).padding(.horizontal, 4)
                    }
                    ForEach(filteredProjects.filter { !$0.isHQ }) { project in
                        projectButton(project)
                    }
                    if filteredProjects.filter({ !$0.isHQ }).isEmpty, search.isEmpty {
                        emptyProjectGuide
                    }
                }.padding(.horizontal, 8)
            }
            .navigationSplitViewColumnWidth(min: 220, ideal: 260, max: 360)
            .safeAreaInset(edge: .top) { searchField }
            .safeAreaInset(edge: .bottom) { statusBar }
            .toolbar {
                ToolbarItem {
                    Button {
                        projectName = ""
                        creatingProject = true
                    } label: {
                        Image(systemName: "square.and.pencil")
                    }.help("New project")
                }
            }
        } detail: {
            ChatView()
                .navigationTitle(selectedProjectName)
                .navigationSubtitle("\(model.snapshot.roles.count + 1) participants")
                .overlay(alignment: .bottom) {
                    if let error = model.errorMessage {
                        Text(error).font(.caption).padding(.horizontal, 12).padding(.vertical, 8)
                            .background(.red.opacity(0.9), in: Capsule()).foregroundStyle(.white)
                            .padding()
                    }
                }
        }
        .confirmationDialog(
            "\(archivingProject?.name ?? "") 방을 닫습니다",
            isPresented: .init(
                get: { archivingProject != nil },
                set: { if !$0 { archivingProject = nil } }
            ),
            titleVisibility: .visible
        ) {
            Button("닫기", role: .destructive) {
                if let project = archivingProject {
                    Task { await model.archiveProject(project.id) }
                }
                archivingProject = nil
            }
            Button("취소", role: .cancel) { archivingProject = nil }
        } message: {
            // 되돌릴 수 없어 보이면 안 누르고, 다 지워진다고 오해해도 안 된다.
            // 무엇이 사라지고 무엇이 남는지 그대로 적는다.
            Text("목록에서 사라지고 배정된 역할이 해제됩니다. 주고받은 메시지는 서버에 남습니다.")
        }
        .sheet(isPresented: $showAgents) {
            AgentsView().frame(minWidth: 780, minHeight: 470)
        }
        .sheet(isPresented: $creatingProject) {
            projectEditor(title: "New project") {
                if await model.createProject(name: projectName) { creatingProject = false }
            }
        }
        .sheet(item: $renamingProject) { project in
            projectEditor(title: "Rename project") {
                if await model.updateProject(id: project.id, name: projectName) {
                    renamingProject = nil
                }
            }
        }
        .fileImporter(
            isPresented: $choosingRepository,
            allowedContentTypes: [.folder], allowsMultipleSelection: false
        ) { result in
            guard let project = repositoryProject,
                  case let .success(urls) = result, let url = urls.first else { return }
            let accessing = url.startAccessingSecurityScopedResource()
            let path = url.path
            if accessing { url.stopAccessingSecurityScopedResource() }
            Task { _ = await model.setProjectRepository(projectID: project.id, path: path) }
        }
        .background(HostedApprovalPresenter(coordinator: model.hostedAgents))
    }

    private func projectButton(_ project: FungisProject) -> some View {
        Button {
            model.selectProject(project.id)
        } label: {
            ProjectRow(
                project: project,
                repository: repository(for: project.id),
                selected: project.id == model.selectedProjectID,
                unread: model.hasUnread(project)
            )
        }
        // hit-area: ProjectRow가 자기 안에서 도형을 준다
        .buttonStyle(.plain)
        .contextMenu { projectMenu(project) }
    }

    /// 처음 온 사람은 HQ 하나만 보게 된다. 그것이 방 목록의 전부로 보이면
    /// 무엇을 해야 하는지 알 길이 없다.
    private var emptyProjectGuide: some View {
        VStack(spacing: 8) {
            Text("아직 프로젝트가 없다")
                .font(.callout.bold())
            Text("HQ는 여러 방을 함께 보는 자리다. 먼저 방을 하나 만든다.")
                .font(.caption).foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button("새 프로젝트", systemImage: "square.and.pencil") {
                projectName = ""
                creatingProject = true
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.small)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 20)
        .padding(.horizontal, 8)
    }

    /// `.searchable(placement: .sidebar)`는 좁은 사이드바에서 돋보기 버튼으로 접히므로
    /// 항상 보이는 필드를 직접 둔다.
    private var searchField: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass").font(.caption).foregroundStyle(.secondary)
            TextField("Search", text: $search)
                .textFieldStyle(.plain).font(.callout)
            if !search.isEmpty {
                Button { search = "" } label: {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(.tertiary)
                        .frame(width: 20, height: 20)
                        .contentShape(Rectangle())
                }.buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 8).padding(.vertical, 5)
        .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 7))
        .padding(.horizontal, 10).padding(.bottom, 8)
    }

    private var statusBar: some View {
        VStack(spacing: 0) {
            Divider()
            HostedApprovalBadge(coordinator: model.hostedAgents)
            Button {
                showAgents = true
            } label: {
                HStack(spacing: 7) {
                    Circle()
                        .fill(statusBarTint)
                        .frame(width: 7, height: 7)
                    Text(agentSummary).font(.caption)
                        .foregroundStyle(awaitingCount > 0 ? Color.orange : .secondary)
                    Spacer()
                    Image(systemName: "chevron.up").font(.caption2).foregroundStyle(.tertiary)
                }
                .contentShape(Rectangle())
                .padding(.horizontal, 14).padding(.vertical, 9)
            }
            .buttonStyle(.plain)
            .help("Agent sessions")
        }
        .background(.bar)
    }

    private var statusBarTint: Color {
        guard model.isConnected else { return .red }
        return awaitingCount > 0 ? .orange : .green
    }

    private var awaitingCount: Int {
        model.snapshot.agents.filter(\.awaitingInput).count
    }

    private var agentSummary: String {
        guard model.isConnected else { return "Disconnected" }
        if awaitingCount > 0 {
            return "\(awaitingCount)개 터미널 확인 필요"
        }
        let online = model.snapshot.targets.filter { target in
            let lifecycle = model.snapshot.statuses.first { $0.id == target.id }?.lifecycle
                ?? target.lifecycle
            return lifecycle != "unknown"
        }.count
        return online == 0 ? "No agents online" : "\(online) agents online"
    }

    private var filteredProjects: [FungisProject] {
        let query = search.trimmingCharacters(in: .whitespacesAndNewlines)
        let projects = query.isEmpty
            ? model.snapshot.projects
            : model.snapshot.projects.filter {
                $0.name.localizedCaseInsensitiveContains(query)
            }
        // HQ가 맨 위에 온다. 전체를 조망하는 자리라 방 하나로 섞이면 묻힌다.
        return projects.sorted { first, second in
            first.isHQ && !second.isHQ
        }
    }

    private var selectedProjectName: String {
        model.snapshot.projects.first { $0.id == model.selectedProjectID }?.name ?? "Fungis"
    }

    private func repository(for projectID: String) -> ProjectRepository? {
        model.snapshot.projectRepositories.first { $0.projectID == projectID }
    }

    @ViewBuilder
    private func projectMenu(_ project: FungisProject) -> some View {
        Button("Rename…") { projectName = project.name; renamingProject = project }
        Button(repository(for: project.id) == nil ? "Choose repository…" : "Change repository…") {
            repositoryProject = project
            choosingRepository = true
        }
        if repository(for: project.id) != nil {
            Button("Remove repository", role: .destructive) {
                Task { _ = await model.deleteProjectRepository(projectID: project.id) }
            }
        }
        Divider()
        Button("방 닫기…", role: .destructive) { archivingProject = project }
    }

    private func projectEditor(title: String, save: @escaping () async -> Void) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(title).font(.title2.bold())
            TextField("Project name", text: $projectName)
            HStack {
                Spacer()
                Button("Save") { Task { await save() } }
                    .buttonStyle(.borderedProminent)
                    .disabled(
                        projectName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    )
            }
        }.padding(24).frame(width: 420)
    }
}

private struct ProjectRow: View {
    let project: FungisProject
    let repository: ProjectRepository?
    let selected: Bool
    let unread: Bool

    var body: some View {
        HStack(spacing: 10) {
            // HQ는 방이 아니라 방들을 보는 자리다. 아바타가 다른 방과 같은
            // 모양이면 목록에서 그냥 한 줄이 된다.
            if project.isHQ {
                Image(systemName: "point.3.connected.trianglepath.dotted")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(selected ? .white : Color.accentColor)
                    .frame(width: 34, height: 34)
                    .background(
                        (selected ? Color.white.opacity(0.18) : Color.accentColor.opacity(0.14)),
                        in: RoundedRectangle(cornerRadius: 9)
                    )
            } else {
                Text(roleInitials(project.name))
                    .font(.system(size: 13, weight: .bold, design: .rounded))
                    .foregroundStyle(.white)
                    .frame(width: 34, height: 34)
                    .background(roleAvatarColor(project.name), in: Circle())
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(project.name)
                    .font(.body.weight(unread ? .semibold : .medium)).lineLimit(1)
                Text(subtitle).font(.caption)
                    .foregroundStyle(selected ? .white.opacity(0.75) : .secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
            if unread {
                Circle().fill(Color.accentColor).frame(width: 8, height: 8)
            }
        }
        .foregroundStyle(selected ? .white : .primary)
        .padding(.horizontal, 8).padding(.vertical, 6)
        .background(
            selected ? Color.accentColor : .clear,
            in: RoundedRectangle(cornerRadius: 8)
        )
        .contentShape(RoundedRectangle(cornerRadius: 8))
    }

    private var subtitle: String {
        // HQ에는 저장소가 없다. "No repository"는 뭔가 빠진 방처럼 보인다.
        if project.isHQ { return "여러 방을 함께 본다" }
        guard let repository else { return "No repository" }
        guard let git = repository.git else { return repository.path }
        return (git.branch ?? "detached HEAD") + (git.dirty ? " · dirty" : "")
    }
}

func statusColor(_ lifecycle: String) -> Color {
    switch lifecycle {
    case "running": .blue
    case "idle", "needs_input": .green
    default: .gray
    }
}
