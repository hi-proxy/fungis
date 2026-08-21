import Foundation
import SwiftUI

/// 한 방에서 누구에게 말하던 중이었는지.
struct RecipientSelection: Codable {
    var targets: Set<String> = []
    var roles: Set<String> = []
    var references: Set<String> = []
}

@MainActor
final class AppModel: ObservableObject {
    let hostedAgents = HostedAgentCoordinator()
    @Published var snapshot = FungisSnapshot.empty
    /// 보드는 방에 속하지 않아서 스냅샷과 따로 받는다.
    @Published var board = BoardSnapshot.empty
    @Published var selectedTargets: Set<String> = []
    @Published var selectedRoles: Set<String> = []
    /// 듣기만 하는 자리. 수신자로 넣으면 받는 쪽이 지시로 읽고 조사에 들어간다.
    @Published var referenceRoles: Set<String> = []
    /// 방마다의 수신자 선택. 방을 옮겨도, 앱을 껐다 켜도 하던 대화의 상대가
    /// 유지된다. 연속성이 이 앱의 값이라 앱 생애에만 남기면 반만 지킨다.
    ///
    /// 안읽음 커서와 같은 성격이라 같은 자리에 둔다 — PM의 방별 화면 상태다.
    /// 여러 기기에서 같은 상태를 봐야 하면 둘을 함께 서버로 옮긴다.
    private var recipientMemory: [String: RecipientSelection] = AppModel.loadRecipients() {
        didSet { AppModel.saveRecipients(recipientMemory) }
    }

    private static let recipientKey = "recipientMemory"

    private static func loadRecipients() -> [String: RecipientSelection] {
        guard let data = UserDefaults.standard.data(forKey: recipientKey),
              let value = try? JSONDecoder().decode(
                  [String: RecipientSelection].self, from: data
              )
        else { return [:] }
        return value
    }

    private static func saveRecipients(_ value: [String: RecipientSelection]) {
        guard let data = try? JSONEncoder().encode(value) else { return }
        UserDefaults.standard.set(data, forKey: recipientKey)
    }
    @Published var isConnected = false
    @Published var errorMessage: String?
    @Published var isMutating = false
    /// 마지막으로 보던 방. 기억하지 않으면 앱을 열 때마다 같은 자리로 가고,
    /// 그 자리가 지워진 방이면 없는 방을 가리킨 채 뜬다.
    ///
    /// 처음이면 HQ 다. HQ 는 항상 있고 빈 HQ 안내가 무엇부터 할지 말해 준다 —
    /// "아무 방도 아닌" 상태를 따로 만들면 타임라인·작성기·보드띠가 각각 그
    /// 갈래를 하나씩 갖게 된다.
    @Published var selectedProjectID = AppModel.lastRoom() {
        didSet { UserDefaults.standard.set(selectedProjectID, forKey: AppModel.roomKey) }
    }

    static let homeRoom = "hq"
    private static let roomKey = "selectedProjectID"

    private static func lastRoom() -> String {
        UserDefaults.standard.string(forKey: roomKey) ?? homeRoom
    }
    @Published private(set) var isLoadingHistory = false
    @Published private(set) var hasOlderMessages = true
    /// 첫 snapshot이 아직 도착하지 않은 구간. 빈 타임라인을 "메시지 없음"으로
    /// 오해시키지 않으려고 구분한다.
    @Published private(set) var isLoadingTimeline = true
    /// 갓 만든 방은 역할을 배정해야 쓸 수 있다. 인스펙터를 Roles로 열어
    /// 다음 할 일을 바로 보여준다.
    @Published var showsRoleSetup = false

    /// CodeSheet 이 파일 한 장을 직접 읽는다. 스냅샷에 실을 값이 아니라
    /// 열었을 때만 가져오는 것이라 모델을 거치지 않는다.
    let api = FungisAPI()
    private var timelineProjectID: String?
    private var prefetchedProjectID: String?
    private var streamTask: Task<Void, Error>?
    private var switchingProject = false
    /// 방을 나가도 읽어둔 메시지를 버리지 않는다. 다시 들어올 때 네트워크를
    /// 기다리지 않고 곧바로 같은 자리를 보여주기 위한 것이다.
    private var timelineCache: [String: (messages: [ChatMessage], hasOlder: Bool)] = [:]
    /// 방마다 어디까지 봤는지. 서버가 주는 last_message_seq와 대조해 안읽음을
    /// 판단한다. 지금은 PM이 이 기기 한 대뿐이라 로컬에 둔다. 여러 기기에서
    /// 같은 상태를 봐야 하면 서버로 옮겨야 한다.
    @Published private var readSeq: [String: Int] =
        UserDefaults.standard.dictionary(forKey: "readSeq") as? [String: Int] ?? [:]

    // 알림 준비는 앱 생애의 일이지 모델의 일이 아니다. init에 두면 앱 번들
    // 밖(테스트)에서 알림 센터를 건드려 프로세스가 죽는다.

    func run() async {
        while !Task.isCancelled {
            let streamingProject = selectedProjectID
            do {
                try await DaemonManager.shared.ensureRunning()
                try await hostedAgents.restorePersistedSessions()
                // WebSocket 첫 push를 기다리지 않고 HTTP로 화면을 먼저 채운다.
                await refresh()
                let task = Task {
                    for try await fresh in api.snapshots(projectID: streamingProject) {
                        try Task.checkCancellation()
                        apply(fresh)
                    }
                }
                streamTask = task
                try await task.value
            } catch {
                if !switchingProject {
                    isConnected = false
                    // 첫 실패는 조용히 재접속한다. 2초 뒤 다시 붙으면 사람이
                    // 알 필요가 없던 일이다. 연속으로 실패할 때만 말한다.
                    streamFailures += 1
                    if streamFailures >= 2 {
                        errorMessage = error.localizedDescription
                    }
                }
            }
            // 프로젝트를 바꿔 끊은 스트림이면 물러서지 않고 바로 다시 붙는다.
            // 취소는 예외로 끝날 수도, for-await가 조용히 끝날 수도 있다.
            if switchingProject {
                switchingProject = false
                continue
            }
            if !Task.isCancelled {
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    func refresh() async {
        guard !isMutating else { return }
        do {
            let fresh = try await api.state(projectID: selectedProjectID)
            apply(fresh)
            await refreshBoard()
        } catch {
            isConnected = false
            errorMessage = error.localizedDescription
        }
    }

    func send(
        _ body: String, to recipients: [String], roles: [String] = [],
        references: [String] = [],
        inReplyTo: Int? = nil,
        track: String? = nil, tags: [String]? = nil,
        inheritContext: Bool = true
    ) async -> Bool {
        await mutate {
            try await api.send(
                projectID: selectedProjectID,
                recipientIDs: recipients, roleIDs: roles,
                referenceIDs: references, body: body,
                inReplyTo: inReplyTo,
                track: track, tags: tags, inheritContext: inheritContext
            )
        }
    }

    /// 참조로 고른 역할의 principal. 배정되지 않은 역할은 보낼 곳이 없다.
    var selectedReferenceIDs: [String] {
        snapshot.roles.filter { referenceRoles.contains($0.id) }.compactMap(\.agentID)
    }

    func createRole(name: String, onboardingPrompt: String) async -> Bool {
        await mutate { try await api.createRole(projectID: selectedProjectID, name: name, onboardingPrompt: onboardingPrompt) }
    }

    func updateRole(id: String, name: String, onboardingPrompt: String) async -> Bool {
        await mutate { try await api.updateRole(id: id, name: name, onboardingPrompt: onboardingPrompt) }
    }

    func deleteRole(id: String) async {
        _ = await mutate { try await api.deleteRole(id: id) }
    }

    func assignRole(id: String, agentID: String, sendOnboarding: Bool) async -> Bool {
        await mutate { try await api.assignRole(id: id, agentID: agentID, sendOnboarding: sendOnboarding) }
    }

    /// 배정 화면에서 연결까지 끝낸다. 미연결 세션을 고르면 먼저 붙이고,
    /// principal(localName)이 잡히면 이어서 배정한다. 예전에는 Agents 화면에
    /// 먼저 다녀와야 해서 온보딩이 화면을 왕복했다.
    func connectAndAssign(
        roleID: String, surfaceID: String, sendOnboarding: Bool
    ) async -> Bool {
        if let localName = connectedLocalName(surfaceID) {
            return await assignRole(
                id: roleID, agentID: localName, sendOnboarding: sendOnboarding
            )
        }
        await agentAction("toggle", surfaceID: surfaceID)
        // 연결은 daemon이 binding을 세운 뒤에야 principal을 준다.
        for _ in 0..<20 {
            if let localName = connectedLocalName(surfaceID) {
                return await assignRole(
                    id: roleID, agentID: localName, sendOnboarding: sendOnboarding
                )
            }
            try? await Task.sleep(for: .milliseconds(150))
            await refresh()
        }
        errorMessage = "세션 연결이 확인되지 않아 배정하지 못했습니다."
        return false
    }

    func assignHosted(
        roleID: String, session: HostedAgentSession, sendOnboarding: Bool
    ) async -> Bool {
        isMutating = true
        defer { isMutating = false }
        do {
            try await hostedAgents.assign(
                session: session, roleID: roleID, projectID: selectedProjectID,
                sendOnboarding: sendOnboarding
            )
            apply(try await api.state(projectID: selectedProjectID))
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func createHostedAndAssign(
        provider: HostedAgentProviderID, roleID: String, cwd: String,
        sendOnboarding: Bool, configuration: HostedAgentConfiguration
    ) async -> Bool {
        isMutating = true
        defer { isMutating = false }
        do {
            _ = try await hostedAgents.createAndAssign(
                provider: provider, cwd: cwd, projectID: selectedProjectID,
                roleID: roleID, sendOnboarding: sendOnboarding,
                configuration: configuration
            )
            apply(try await api.state(projectID: selectedProjectID))
            return true
        } catch is CancellationError {
            return false
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func stopHosted(_ session: HostedAgentSession) async {
        isMutating = true
        defer { isMutating = false }
        await hostedAgents.stop(session)
        do {
            apply(try await api.state(projectID: selectedProjectID))
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func connectedLocalName(_ surfaceID: String) -> String? {
        guard let agent = snapshot.agents.first(where: { $0.surfaceID == surfaceID }),
              agent.connected, let localName = agent.localName else { return nil }
        return localName
    }

    func unassignRole(id: String) async {
        _ = await mutate { try await api.unassignRole(id: id) }
    }

    func roleHistory(id: String) async -> [RoleAssignment] {
        do { return try await api.roleHistory(id: id) }
        catch { errorMessage = error.localizedDescription; return [] }
    }

    func putRoleAvatar(id: String, data: Data, mediaType: String) async -> Bool {
        await mutate { try await api.putRoleAvatar(id: id, data: data, mediaType: mediaType) }
    }

    func deleteRoleAvatar(id: String) async -> Bool {
        await mutate { try await api.deleteRoleAvatar(id: id) }
    }

    func agentAction(_ action: String, surfaceID: String) async {
        _ = await mutate { try await api.act(on: surfaceID, action: action) }
    }

    func setNickname(localName: String, nickname: String) async -> Bool {
        await mutate { try await api.setNickname(localName: localName, nickname: nickname) }
    }

    func saveShared(key: String, value: String) async -> Bool {
        await mutate { try await api.putShared(projectID: selectedProjectID, key: key, value: value) }
    }

    func deleteShared(key: String) async {
        _ = await mutate { try await api.deleteShared(projectID: selectedProjectID, key: key) }
    }

    /// 이 프로젝트의 담당자가 배정된 역할 전부에 짧은 `fungis init` 호출문을
    /// 보낸다. 사용법 본문은 에이전트가 bootstrap API에서 읽는다.
    ///
    /// 채팅 입력창의 수신자 선택과 엮지 않는다. 그쪽은 대화 맥락이라 수시로
    /// 바뀌는데 이건 셋업 행위다. 엮어 두면 대화하려고 고른 상대에게 셋업
    /// 메시지가 나간다.
    func initializeChat() async {
        let roles = snapshot.roles.filter(\.assigned).map(\.id)
        guard !roles.isEmpty else { return }
        _ = await send(
            "[fungis:init] 사용법과 현재 역할 구성을 불러오세요: "
                + "fungis init --project \(selectedProjectID)",
            to: [],
            roles: roles,
            tags: ["fungis-init"],
            inheritContext: false
        )
    }

    /// 지금 열어 둔 방은 항상 읽은 것으로 본다.
    func hasUnread(_ project: FungisProject) -> Bool {
        guard let last = project.lastMessageSeq else { return false }
        guard project.id != selectedProjectID else { return false }
        return last > (readSeq[project.id] ?? 0)
    }

    private func markRead(_ projectID: String, upTo seq: Int) {
        guard readSeq[projectID] ?? 0 < seq else { return }
        readSeq[projectID] = seq
        UserDefaults.standard.set(readSeq, forKey: "readSeq")
    }

    func resolvePermission(_ request: PermissionRequest, allow: Bool) async {
        _ = await mutate {
            try await api.resolvePermission(
                requestID: request.id, projectID: selectedProjectID,
                status: allow ? "allowed" : "denied"
            )
        }
    }

    /// 방을 목록에서 치운다. 메시지는 서버에 남는다 — 방을 닫는 것과 오간 말을
    /// 없애는 것은 다른 일이다. 지금 보던 방이면 다른 방으로 옮겨 준다.
    func archiveProject(_ id: String) async {
        guard await mutate({ try await api.archiveProject(id: id) }) else { return }
        recipientMemory[id] = nil
        timelineCache[id] = nil
        // 지운 방에 서 있었으면 HQ 로 간다. 남은 방 중 아무거나 고르면 남의
        // 방에 떨어져 거기 대고 말하게 된다.
        if selectedProjectID == id {
            selectProject(Self.homeRoom)
        }
        await refresh()
    }

    func selectProject(_ id: String) {
        guard id != selectedProjectID else { return }
        let previous = selectedProjectID
        timelineCache[selectedProjectID] = (Array(snapshot.timeline.suffix(10)), hasOlderMessages)
        selectedProjectID = id
        if let cached = timelineCache[id] {
            // 들어갈 때는 최신 10건만 붙인다. 쌓아둔 과거까지 한꺼번에 붙이면
            // 보이지도 않는 행을 전부 다시 레이아웃하게 된다.
            snapshot.timeline = Array(cached.messages.suffix(10))
            hasOlderMessages = cached.hasOlder || cached.messages.count > 10
            // 캐시를 살려 두고 refresh 결과를 병합한다.
            timelineProjectID = id
            prefetchedProjectID = nil
            isLoadingTimeline = false
        } else {
            snapshot.timeline = []
            hasOlderMessages = true
            timelineProjectID = nil
            prefetchedProjectID = nil
            isLoadingTimeline = true
        }
        // 수신자 선택은 방마다 기억한다. 방을 옮겼다 돌아왔을 때 누구에게
        // 말하던 중이었는지 다시 고르게 하면, 옮길 때마다 그 일을 반복한다.
        recipientMemory[previous] = RecipientSelection(
            targets: selectedTargets, roles: selectedRoles, references: referenceRoles
        )
        let remembered = recipientMemory[id] ?? RecipientSelection()
        selectedTargets = remembered.targets
        selectedRoles = remembered.roles
        referenceRoles = remembered.references
        // 옛 프로젝트 스트림은 서버가 다음 snapshot을 보낼 때까지 스스로 끝나지
        // 않는다. 끊어야 run 루프가 새 프로젝트로 곧바로 다시 붙는다.
        switchingProject = true
        streamTask?.cancel()
    }

    func createProject(name: String) async -> Bool {
        do {
            let project = try await api.createProject(name: name)
            // 직접 대입하면 수신자 선택과 스트림이 이전 방에 남는다.
            // 방 전환 경로를 그대로 탄다.
            selectProject(project.id)
            showsRoleSetup = true
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func updateProject(id: String, name: String) async -> Bool {
        await mutate { _ = try await api.updateProject(id: id, name: name) }
    }

    func setProjectRepository(projectID: String, path: String) async -> Bool {
        await mutate { try await api.setProjectRepository(projectID: projectID, path: path) }
    }

    func deleteProjectRepository(projectID: String) async -> Bool {
        await mutate { try await api.deleteProjectRepository(projectID: projectID) }
    }

    func updatePMProfile(displayName: String) async -> Bool {
        await mutate { try await api.updatePMProfile(displayName: displayName) }
    }

    func putPMAvatar(data: Data, mediaType: String) async -> Bool {
        await mutate { try await api.putPMAvatar(data: data, mediaType: mediaType) }
    }

    func deletePMAvatar() async -> Bool {
        await mutate { try await api.deletePMAvatar() }
    }

    func createBookmark(messageSeq: Int, label: String) async -> Bool {
        await mutate {
            try await api.createBookmark(
                projectID: selectedProjectID, messageSeq: messageSeq, label: label
            )
        }
    }

    func deleteBookmark(id: String) async {
        _ = await mutate {
            try await api.deleteBookmark(projectID: selectedProjectID, bookmarkID: id)
        }
    }

    func createTimelinePin(afterMessageSeq: Int, label: String) async -> Bool {
        await mutate {
            try await api.createTimelinePin(
                projectID: selectedProjectID,
                afterMessageSeq: afterMessageSeq,
                label: label
            )
        }
    }

    func deleteTimelinePin(id: String) async {
        _ = await mutate {
            try await api.deleteTimelinePin(projectID: selectedProjectID, pinID: id)
        }
    }

    func loadOlderMessages() async {
        guard !isLoadingHistory, hasOlderMessages,
              timelineProjectID == selectedProjectID,
              let earliestSequence = snapshot.timeline.first?.seq else { return }
        let projectID = selectedProjectID
        isLoadingHistory = true
        defer { isLoadingHistory = false }
        do {
            let page = try await api.history(
                projectID: projectID, before: earliestSequence, limit: 50
            )
            guard projectID == selectedProjectID,
                  timelineProjectID == projectID else { return }
            var updated = snapshot
            updated.timeline = MessageTimeline.merging(updated.timeline, page)
            snapshot = updated
            if page.count < 50 { hasOlderMessages = false }
            timelineCache[projectID] = (Array(updated.timeline.suffix(10)), hasOlderMessages)
        } catch {
            guard projectID == selectedProjectID else { return }
            errorMessage = error.localizedDescription
        }
    }

    func ensureMessageLoaded(_ sequence: Int) async {
        while !snapshot.timeline.contains(where: { $0.seq == sequence }),
              hasOlderMessages, timelineProjectID == selectedProjectID {
            if isLoadingHistory {
                try? await Task.sleep(for: .milliseconds(50))
                continue
            }
            let previousEarliest = snapshot.timeline.first?.seq
            await loadOlderMessages()
            if snapshot.timeline.first?.seq == previousEarliest { break }
        }
    }

    private func mutate(_ operation: () async throws -> Void) async -> Bool {
        isMutating = true
        defer { isMutating = false }
        do {
            try await operation()
            let fresh = try await api.state(projectID: selectedProjectID)
            apply(fresh)
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    /// 테스트가 화면 없이 갱신 경로를 밟게 한다. 수신자 유지가 세 번 어긋난
    /// 자리라 논리만 따로 붙잡을 수 있어야 한다.
    func applyForTesting(_ freshSnapshot: FungisSnapshot) { apply(freshSnapshot) }

    // MARK: - 상황보드
    //
    // 스냅샷 스트림에 얹지 않고 따로 당긴다. 보드는 방에 속하지 않아서
    // 방 스냅샷에 넣으면 한 글자 바뀔 때마다 열려 있는 모든 방이 다시 흐른다.

    func refreshBoard() async {
        guard let fresh = try? await api.board() else { return }
        board = fresh
    }

    func connectTrack(projectID: String) async -> Bool {
        guard let hq = board.hq else { return false }
        return await runBoard {
            try await self.api.connectTrack(projectID: projectID, hqID: hq.id)
        }
    }

    func disconnectTrack(projectID: String) async -> Bool {
        await runBoard { try await self.api.disconnectTrack(projectID: projectID) }
    }

    func addBoardNode(projectID: String, title: String) async -> Bool {
        await runBoard {
            try await self.api.createBoardNode(projectID: projectID, title: title)
        }
    }

    func setBoardNodeStatus(nodeID: String, status: String) async -> Bool {
        await runBoard {
            try await self.api.updateBoardNode(nodeID: nodeID, status: status)
        }
    }

    func removeBoardNode(nodeID: String) async -> Bool {
        await runBoard { try await self.api.deleteBoardNode(nodeID: nodeID) }
    }

    func linkBoardNodes(nodeID: String, waitsFor: String) async -> Bool {
        await runBoard {
            try await self.api.linkBoardNodes(nodeID: nodeID, waitsFor: waitsFor)
        }
    }

    func unlinkBoardNodes(nodeID: String, waitsFor: String) async -> Bool {
        await runBoard {
            try await self.api.unlinkBoardNodes(nodeID: nodeID, waitsFor: waitsFor)
        }
    }

    func setRoleLead(roleID: String, isLead: Bool) async -> Bool {
        // 저장중 표시. 소집 모달이 이 동안 lead 칩을 비활성화한다.
        // refresh()는 isMutating이면 물러서므로 끄고 나서 부른다.
        isMutating = true
        let ok = await runBoard {
            try await self.api.setRoleLead(roleID: roleID, isLead: isLead)
        }
        isMutating = false
        if ok { await refresh() }
        return ok
    }

    /// 소집 모달이 닫힐 때 한 번 부른다. 모달이 열리기 전과 닫힌 후의
    /// lead 차이만 서버가 그 담당자에게 안내한다. 즉시 보내면 모달 안에서
    /// 갈아탄 앞사람이 안내를 받아 놓고 lead가 아니게 된다.
    func flushLeadAnnouncements() async {
        do { try await api.flushLeadAnnouncements() }
        catch { errorMessage = String(describing: error) }
    }

    /// 보드를 고치는 것은 전부 같은 모양이다 — 해보고, 되면 다시 읽는다.
    /// 순환이나 lead 없음처럼 서버가 거절하는 경우가 있어서 메시지를 살린다.
    private func runBoard(_ body: () async throws -> Void) async -> Bool {
        do {
            try await body()
            await refreshBoard()
            return true
        } catch {
            errorMessage = String(describing: error)
            return false
        }
    }

    private var streamFailures = 0

    private func apply(_ freshSnapshot: FungisSnapshot) {
        streamFailures = 0
        var fresh = freshSnapshot
        guard fresh.projectID == selectedProjectID else { return }
        let isNewTimeline = timelineProjectID != fresh.projectID
        if isNewTimeline {
            timelineProjectID = fresh.projectID
            hasOlderMessages = fresh.timeline.count == 10
        } else {
            fresh.timeline = MessageTimeline.merging(snapshot.timeline, fresh.timeline)
        }
        snapshot = fresh
        isLoadingTimeline = false
        // 고르고 있는 방이 목록에 없으면 HQ 로 간다.
        //
        // 기억해 둔 방이 그 사이 지워졌을 수 있다. 그대로 두면 헤더에는 이름이
        // 보이는데 좌측 목록에서는 아무것도 안 골라져 있다 — 서버가 그 id 로도
        // 스냅샷을 내주기 때문에 화면은 정상처럼 보인다.
        //
        // 아무 방이나 첫 번째를 고르지 않는다. 임의로 고르면 남의 방에 떨어져
        // 거기 대고 말하게 된다. HQ 는 어느 방도 아니면서 항상 있다.
        if !fresh.projects.contains(where: { $0.id == fresh.projectID }) {
            selectProject(Self.homeRoom)
            return
        }
        if let seq = fresh.projects.first(where: { $0.id == fresh.projectID })?.lastMessageSeq {
            markRead(fresh.projectID, upTo: seq)
        }
        timelineCache[fresh.projectID] = (Array(fresh.timeline.suffix(10)), hasOlderMessages)
        NotificationCoordinator.shared.consume(freshSnapshot)
        let available = Set(fresh.targets.map(\.id))
        selectedTargets.formIntersection(available)
        let availableRoles = Set(fresh.roles.map(\.id))
        selectedRoles.formIntersection(availableRoles)
        referenceRoles.formIntersection(availableRoles)
        // 역할은 이 프로젝트 소속이라 자동으로 골라도 안전하다. 세션 목록은
        // 전역이므로 자동으로 고르면 다른 방 담당에게 발송될 수 있다.
        //
        // 처음 들어온 방에서만 고른다. 매번 고르면 PM이 지운 선택이 되살아나고,
        // 참조만 남긴 상태도 수신자가 붙어 되돌아온다 — 방마다 기억해 둔 것이
        // 그때마다 덮인다.
        if recipientMemory[fresh.projectID] == nil,
           selectedTargets.isEmpty, selectedRoles.isEmpty, referenceRoles.isEmpty,
           let firstRole = fresh.roles.first?.id {
            selectedRoles.insert(firstRole)
        }
        recipientMemory[fresh.projectID] = RecipientSelection(
            targets: selectedTargets, roles: selectedRoles, references: referenceRoles
        )
        isConnected = true
        errorMessage = nil
        // 타임라인이 역순으로 쌓이므로 과거는 레이아웃 뒤쪽에 붙는다. 이미
        // 배치된 행을 다시 재지 않아 진입 직후 병합이 다시 가능해졌다.
        // 타임라인이 역순으로 쌓이므로 과거는 레이아웃 뒤쪽에 붙는다. 이미
        // 배치된 행을 다시 재지 않아 진입 직후 병합이 다시 가능해졌다.
        if hasOlderMessages, prefetchedProjectID != fresh.projectID {
            prefetchedProjectID = fresh.projectID
            Task { await loadOlderMessages() }
        }
    }
}
