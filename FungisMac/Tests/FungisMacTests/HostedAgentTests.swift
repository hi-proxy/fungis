import Foundation
import Testing
@testable import FungisMac

@Test func chatGPTAccountKeepsPlanAndEmail() {
    let account = HostedAgentAccount.decode([
        "requiresOpenaiAuth": true,
        "account": [
            "type": "chatgpt",
            "planType": "plus",
            "email": "pm@example.com",
        ],
    ])

    #expect(account.authentication == .chatGPT(plan: "plus", email: "pm@example.com"))
    #expect(account.requiresOpenAIAuthentication)
}

@Test func anEmptyAccountMeansTheUserMustSignIn() {
    let account = HostedAgentAccount.decode([
        "requiresOpenaiAuth": true,
        "account": NSNull(),
    ])

    #expect(account.authentication == .signedOut)
}

@Test func providerCatalogLeavesClaudeBehindTheSameEntryPoint() {
    #expect(HostedAgentProviderID.codex.isAvailable)
    #expect(!HostedAgentProviderID.claudeCode.isAvailable)
    #expect(HostedAgentProviderID.allCases.map(\.id) == ["codex", "claude-code"])
}

@Test func codexModelCatalogKeepsOnlyAdvertisedReasoningChoices() throws {
    let model = try HostedModelOption.decode([
        "id": "gpt-5.6-sol", "model": "gpt-5.6-sol",
        "displayName": "GPT-5.6 Codex", "description": "Frontier coding model",
        "isDefault": true, "defaultReasoningEffort": "high",
        "supportedReasoningEfforts": [
            ["reasoningEffort": "medium", "description": "Balanced"],
            ["reasoningEffort": "high", "description": "Deeper reasoning"],
        ],
    ])
    #expect(model.id == "gpt-5.6-sol")
    #expect(model.defaultReasoningEffort == "high")
    #expect(model.supportedReasoningEfforts.map(\.effort) == ["medium", "high"])
}

@Test func hostedToolActivityKeepsCommandBoundaryAndCompletion() throws {
    let started = try #require(HostedAgentActivity.decode(item: [
        "id": "item-1", "type": "commandExecution",
        "command": "swift test", "cwd": "/tmp/project", "status": "inProgress",
    ], completed: false))
    let completed = try #require(HostedAgentActivity.decode(item: [
        "id": "item-1", "type": "commandExecution",
        "command": "swift test", "cwd": "/tmp/project", "status": "completed",
    ], completed: true))
    #expect(started.title == "Command")
    #expect(started.detail == "swift test")
    #expect(started.state == .running)
    #expect(completed.id == started.id)
    #expect(completed.state == .succeeded)
}

@Test func legacyHostedRecoveryRecordLeavesModelConfigurationUnset() throws {
    let data = Data("""
    {
      "principal_id": "agent-legacy",
      "local_name": "codex-hosted-legacy",
      "provider": "codex",
      "session_id": "thread-legacy",
      "cwd": "/tmp/project",
      "project_id": "project-1"
    }
    """.utf8)
    let record = try JSONDecoder().decode(HostedAgentRecoveryRecord.self, from: data)
    #expect(record.model == nil)
    #expect(record.reasoningEffort == nil)
}

@Test func installedCodexAdvertisesModelsAndReasoningChoices() async throws {
    guard ProcessInfo.processInfo.environment["FUNGIS_RUN_CODEX_MODELS_TEST"] == "1" else {
        return
    }
    let client = CodexAppServerClient()
    do {
        _ = try await client.start()
        let models = try await client.models()
        #expect(!models.isEmpty)
        #expect(models.contains { $0.isDefault })
        #expect(models.allSatisfy { model in
            model.supportedReasoningEfforts.contains {
                $0.effort == model.defaultReasoningEffort
            }
        })
        let selected = models.first(where: { $0.isDefault }) ?? models[0]
        let configuration = HostedAgentConfiguration(
            model: selected.id, reasoningEffort: selected.defaultReasoningEffort
        )
        let threadID = try await client.startThread(
            cwd: FileManager.default.temporaryDirectory.path,
            configuration: configuration
        )
        _ = try await client.runTurn(
            threadID: threadID,
            text: "Reply with exactly MODEL_CONFIGURATION_OK and do not use tools."
        )
        #expect(try await client.resumeThread(
            threadID: threadID, cwd: FileManager.default.temporaryDirectory.path,
            configuration: configuration
        ) == threadID)
    } catch {
        await client.stop()
        throw error
    }
    await client.stop()
}

@Test func explicitCodexPathWinsOverFinderPath() throws {
    let directory = FileManager.default.temporaryDirectory
        .appending(path: UUID().uuidString, directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let executable = directory.appending(path: "codex")
    try Data("#!/bin/sh\nexit 0\n".utf8).write(to: executable)
    try FileManager.default.setAttributes(
        [.posixPermissions: 0o755], ofItemAtPath: executable.path
    )

    let found = HostedExecutableResolver.codexURL(environment: [
        "FUNGIS_CODEX_PATH": executable.path,
        "PATH": "/does/not/exist",
    ])
    #expect(found == executable)
}

@Test func hostedWorkspaceRequiresARealNonRootDirectory() throws {
    let directory = FileManager.default.temporaryDirectory
        .appending(path: UUID().uuidString, directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: directory) }

    #expect(HostedWorkspaceDirectory.validatedPath(nil) == nil)
    #expect(HostedWorkspaceDirectory.validatedPath("") == nil)
    #expect(HostedWorkspaceDirectory.validatedPath("/") == nil)
    #expect(HostedWorkspaceDirectory.validatedPath(directory.path) == directory.path)
    #expect(HostedWorkspaceDirectory.validatedPath(directory.appending(path: "missing").path) == nil)
}

@Test func installedCodexAppServerReportsChatGPTSubscription() async throws {
    guard ProcessInfo.processInfo.environment["FUNGIS_RUN_CODEX_HOST_TEST"] == "1" else {
        return
    }
    let client = CodexAppServerClient()
    do {
        let account = try await client.start()
        #expect(account.authentication.isChatGPT)
    } catch {
        await client.stop()
        throw error
    }
    await client.stop()
}

@Test func installedCodexAppServerCompletesAHostedTurn() async throws {
    guard ProcessInfo.processInfo.environment["FUNGIS_RUN_CODEX_HOST_TURN_TEST"] == "1" else {
        return
    }
    let client = CodexAppServerClient()
    do {
        let account = try await client.start()
        #expect(account.authentication.isChatGPT)
        let threadID = try await client.startThread(
            cwd: FileManager.default.temporaryDirectory.path
        )
        let answer = try await client.runTurn(
            threadID: threadID,
            text: "Reply with exactly HOSTED_OK and do not use tools."
        )
        #expect(answer == "HOSTED_OK")
    } catch {
        await client.stop()
        throw error
    }
    await client.stop()
}

@Test func installedCodexAppServerResumesAPersistedThread() async throws {
    guard ProcessInfo.processInfo.environment["FUNGIS_RUN_CODEX_RESUME_TEST"] == "1" else {
        return
    }
    let cwd = FileManager.default.temporaryDirectory.path
    let first = CodexAppServerClient()
    let threadID: String
    do {
        _ = try await first.start()
        threadID = try await first.startThread(cwd: cwd)
        _ = try await first.runTurn(
            threadID: threadID,
            text: "Reply with exactly RESUME_READY and do not use tools."
        )
    } catch {
        await first.stop()
        throw error
    }
    await first.stop()

    let resumed = CodexAppServerClient()
    do {
        _ = try await resumed.start()
        #expect(try await resumed.resumeThread(threadID: threadID, cwd: cwd) == threadID)
    } catch {
        await resumed.stop()
        throw error
    }
    await resumed.stop()
}

@Test func installedCodexAppServerInterruptsARunningTurn() async throws {
    guard ProcessInfo.processInfo.environment["FUNGIS_RUN_CODEX_INTERRUPT_TEST"] == "1" else {
        return
    }
    let client = CodexAppServerClient()
    do {
        _ = try await client.start()
        let threadID = try await client.startThread(
            cwd: FileManager.default.temporaryDirectory.path
        )
        do {
            _ = try await client.runTurn(
                threadID: threadID,
                text: "Write a detailed essay with at least 2000 words. Do not use tools."
            ) { event in
                if case let .started(turnID) = event {
                    Task {
                        try? await Task.sleep(for: .milliseconds(300))
                        try? await client.interruptTurn(threadID: threadID, turnID: turnID)
                    }
                }
            }
            Issue.record("interrupted turn returned a successful answer")
        } catch HostedAgentError.turnInterrupted {
            // Expected: the provider confirms interruption via turn/completed.
        }
    } catch {
        await client.stop()
        throw error
    }
    await client.stop()
}

@Test func installedCodexToolsReceiveOnlyTheirHostedFungisIdentity() async throws {
    guard ProcessInfo.processInfo.environment["FUNGIS_RUN_CODEX_IDENTITY_TEST"] == "1" else {
        return
    }
    let client = CodexAppServerClient()
    let identity = HostedAgentIdentity(
        principalID: "agent-hosted-identity-test", projectID: "project-identity-test"
    )
    do {
        try await client.configure(identity: identity)
        _ = try await client.start()
        let threadID = try await client.startThread(
            cwd: FileManager.default.temporaryDirectory.path
        )
        let answer = try await client.runTurn(
            threadID: threadID,
            text: """
            Run /usr/bin/printenv FUNGIS_HOSTED_PRINCIPAL_ID and then \
            /usr/bin/printenv FUNGIS_HOSTED_PROJECT_ID. Do not run any other command. \
            Reply with exactly the two values joined by a vertical bar.
            """
        )
        #expect(answer == "agent-hosted-identity-test|project-identity-test")
    } catch {
        await client.stop()
        throw error
    }
    await client.stop()
}

private actor ApprovalRecorder {
    private(set) var requests: [HostedApprovalRequest] = []
    func record(_ request: HostedApprovalRequest) { requests.append(request) }
}

private actor TurnEventRecorder {
    private(set) var events: [HostedAgentTurnEvent] = []
    func record(_ event: HostedAgentTurnEvent) { events.append(event) }
}

@Test func installedCodexStreamsCommandActivity() async throws {
    guard ProcessInfo.processInfo.environment["FUNGIS_RUN_CODEX_ACTIVITY_TEST"] == "1" else {
        return
    }
    let client = CodexAppServerClient()
    let recorder = TurnEventRecorder()
    do {
        await client.configureApprovalHandler { _ in .allowOnce }
        _ = try await client.start()
        let threadID = try await client.startThread(
            cwd: FileManager.default.temporaryDirectory.path
        )
        _ = try await client.runTurn(
            threadID: threadID,
            text: "Run exactly /bin/pwd once, then reply DONE."
        ) { event in
            await recorder.record(event)
        }
        let activities = await recorder.events.compactMap { event in
            if case let .activity(activity) = event { return activity }
            return nil
        }
        #expect(activities.contains { $0.title == "Command" && $0.state == .running })
        #expect(activities.contains { $0.title == "Command" && $0.state == .succeeded })
    } catch {
        await client.stop()
        throw error
    }
    await client.stop()
}

@Test func installedCodexRelaysAnOutOfWorkspaceApprovalRequest() async throws {
    guard ProcessInfo.processInfo.environment["FUNGIS_RUN_CODEX_APPROVAL_TEST"] == "1" else {
        return
    }
    let client = CodexAppServerClient()
    let recorder = ApprovalRecorder()
    let identity = HostedAgentIdentity(
        principalID: "agent-hosted-approval-test", projectID: "project-approval-test"
    )
    do {
        try await client.configure(identity: identity)
        await client.configureApprovalHandler { request in
            await recorder.record(request)
            return .deny
        }
        _ = try await client.start()
        let workspace = FileManager.default.temporaryDirectory
            .appending(path: UUID().uuidString, directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: workspace, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: workspace) }
        let threadID = try await client.startThread(cwd: workspace.path)
        let answer = try await client.runTurn(
            threadID: threadID,
            text: """
            Run exactly this command once: /usr/bin/curl -I https://example.com. \
            Do not use another tool. If permission is denied, just say DENIED_OK.
            """
        )
        let requests = await recorder.requests
        if requests.isEmpty {
            throw HostedAgentError.rpc("승인 요청 없이 끝난 실제 turn: \(answer)")
        }
        #expect(requests.count == 1)
        #expect(requests.first?.kind == .command)
        #expect(requests.first?.command?.contains("example.com") == true)
    } catch {
        await client.stop()
        throw error
    }
    await client.stop()
}

private actor FakeHostedProviderClient: HostedAgentProviderClient {
    let threadID: String
    private(set) var identity: HostedAgentIdentity?
    private(set) var stopped = false
    private(set) var startedCwds: [String] = []
    private(set) var resumedThreads: [(String, String)] = []
    private(set) var startedConfigurations: [HostedAgentConfiguration?] = []
    private(set) var resumedConfigurations: [HostedAgentConfiguration?] = []
    private var approvalHandler: HostedApprovalHandler?
    private var exited = false
    private var exitWaiters: [CheckedContinuation<Void, Never>] = []

    init(threadID: String) { self.threadID = threadID }

    func configure(identity: HostedAgentIdentity) async throws { self.identity = identity }
    func configureApprovalHandler(_ handler: @escaping HostedApprovalHandler) async {
        approvalHandler = handler
    }
    func start() async throws -> HostedAgentAccount { accountValue }
    func account() async throws -> HostedAgentAccount { accountValue }
    func beginChatGPTLogin() async throws -> URL { URL(string: "https://example.com/login")! }
    func startThread(cwd: String) async throws -> String {
        startedCwds.append(cwd)
        return threadID
    }
    func startThread(
        cwd: String, configuration: HostedAgentConfiguration?
    ) async throws -> String {
        startedConfigurations.append(configuration)
        return try await startThread(cwd: cwd)
    }
    func resumeThread(threadID: String, cwd: String) async throws -> String {
        resumedThreads.append((threadID, cwd))
        return threadID
    }
    func resumeThread(
        threadID: String, cwd: String, configuration: HostedAgentConfiguration?
    ) async throws -> String {
        resumedConfigurations.append(configuration)
        return try await resumeThread(threadID: threadID, cwd: cwd)
    }
    func runTurn(
        threadID: String, text: String, onEvent: HostedTurnEventHandler?
    ) async throws -> String {
        await onEvent?(.started(turnID: "turn-fake"))
        return ""
    }
    func interruptTurn(threadID: String, turnID: String) async throws {}
    func waitForExit() async {
        if exited { return }
        await withCheckedContinuation { exitWaiters.append($0) }
    }
    func stop() async {
        stopped = true
        finishExit()
    }

    func crash() { finishExit() }

    private func finishExit() {
        guard !exited else { return }
        exited = true
        let waiters = exitWaiters
        exitWaiters.removeAll()
        for waiter in waiters { waiter.resume() }
    }

    func askForApproval(_ request: HostedApprovalRequest) async -> HostedApprovalDecision {
        await approvalHandler?(request) ?? .cancel
    }

    private var accountValue: HostedAgentAccount {
        HostedAgentAccount(
            authentication: .chatGPT(plan: "plus", email: nil),
            requiresOpenAIAuthentication: true
        )
    }
}

private final class FakeHostedProviderFactory: @unchecked Sendable {
    private let lock = NSLock()
    private var nextID = 1
    private var storage: [FakeHostedProviderClient] = []

    func make() -> any HostedAgentProviderClient {
        lock.lock()
        defer { lock.unlock() }
        let client = FakeHostedProviderClient(threadID: "thread-\(nextID)")
        nextID += 1
        storage.append(client)
        return client
    }

    func client(at index: Int) -> FakeHostedProviderClient {
        lock.lock()
        defer { lock.unlock() }
        return storage[index]
    }

    var count: Int {
        lock.lock()
        defer { lock.unlock() }
        return storage.count
    }
}

private actor StreamingFakeHostedProviderClient: HostedAgentProviderClient {
    private(set) var interrupted: (threadID: String, turnID: String)?
    private var turnWaiter: CheckedContinuation<Void, Never>?
    private var exitWaiters: [CheckedContinuation<Void, Never>] = []
    private var exited = false

    func configure(identity: HostedAgentIdentity) async throws {}
    func configureApprovalHandler(_ handler: @escaping HostedApprovalHandler) async {}
    func start() async throws -> HostedAgentAccount { accountValue }
    func account() async throws -> HostedAgentAccount { accountValue }
    func beginChatGPTLogin() async throws -> URL { URL(string: "https://example.com")! }
    func startThread(cwd: String) async throws -> String { "thread-stream" }
    func resumeThread(threadID: String, cwd: String) async throws -> String { threadID }

    func runTurn(
        threadID: String, text: String, onEvent: HostedTurnEventHandler?
    ) async throws -> String {
        await onEvent?(.started(turnID: "turn-stream"))
        await onEvent?(.activity(HostedAgentActivity(
            id: "tool-1", title: "Command", detail: "swift test", state: .running
        )))
        await onEvent?(.activity(HostedAgentActivity(
            id: "tool-1", title: "Command", detail: "swift test", state: .succeeded
        )))
        await onEvent?(.delta("부분 응답"))
        await withCheckedContinuation { turnWaiter = $0 }
        throw HostedAgentError.turnInterrupted
    }

    func interruptTurn(threadID: String, turnID: String) async throws {
        interrupted = (threadID, turnID)
        turnWaiter?.resume()
        turnWaiter = nil
    }

    func waitForExit() async {
        if exited { return }
        await withCheckedContinuation { exitWaiters.append($0) }
    }

    func stop() async {
        turnWaiter?.resume()
        turnWaiter = nil
        guard !exited else { return }
        exited = true
        let waiters = exitWaiters
        exitWaiters.removeAll()
        for waiter in waiters { waiter.resume() }
    }

    private var accountValue: HostedAgentAccount {
        HostedAgentAccount(
            authentication: .chatGPT(plan: "plus", email: nil),
            requiresOpenAIAuthentication: true
        )
    }
}

private actor FakeHostedAgentAPI: HostedAgentAPIClient {
    private(set) var connected: [String] = []
    private(set) var disconnected: [String] = []
    private(set) var assignments: [(roleID: String, agentID: String)] = []
    private(set) var createdApprovals: [HostedApprovalRequest] = []
    private(set) var resolvedApprovals: [(String, String, String?)] = []
    private(set) var recoveryRecords: [HostedAgentRecoveryRecord] = []
    private(set) var disconnectForgetValues: [Bool] = []
    private(set) var inboxMessages: [HostedInboxMessage] = []
    private(set) var replies: [String] = []
    private(set) var ackedThrough: [Int] = []

    func setRecoveryRecords(_ records: [HostedAgentRecoveryRecord]) {
        recoveryRecords = records
    }

    func setInboxMessages(_ messages: [HostedInboxMessage]) {
        inboxMessages = messages
    }

    func assignRole(id: String, agentID: String, sendOnboarding: Bool) async throws {
        assignments.append((id, agentID))
    }

    func recoverableHostedSessions() async throws -> [HostedAgentRecoveryRecord] {
        recoveryRecords
    }

    func connectHostedSession(_ session: HostedAgentSession) async throws {
        connected.append(session.principalID)
    }

    func disconnectHostedSession(_ principalID: String, forget: Bool) async throws {
        disconnected.append(principalID)
        disconnectForgetValues.append(forget)
    }

    func hostedInbox(principalID: String, after: Int) async throws -> [HostedInboxMessage] {
        inboxMessages.filter { $0.seq > after }
    }

    func replyFromHosted(
        principalID: String, projectID: String, recipientID: String,
        body: String, inReplyToProjectSeq: Int
    ) async throws { replies.append(body) }

    func ackHosted(principalID: String, through: Int) async throws {
        ackedThrough.append(through)
    }

    func createHostedPermission(_ approval: HostedApprovalRequest) async throws -> String {
        createdApprovals.append(approval)
        return "audit-\(createdApprovals.count)"
    }

    func resolvePermission(
        requestID: String, projectID: String, status: String,
        decision: String?, decisionScope: String?
    ) async throws {
        resolvedApprovals.append((requestID, status, decisionScope))
    }
}

@Test func codexApprovalRequestKeepsExactCommandAndBoundary() throws {
    let request = try HostedApprovalRequest.decode(
        method: "item/commandExecution/requestApproval", requestID: .integer(42),
        params: [
            "threadId": "thread-1", "turnId": "turn-1", "itemId": "item-1",
            "reason": "needs network", "command": ["curl", "https://example.com"],
            "cwd": "/tmp/project",
            "networkApprovalContext": ["host": "example.com", "protocol": "https"],
            "availableDecisions": ["accept", "acceptForSession", "decline"],
        ],
        identity: HostedAgentIdentity(principalID: "agent-1", projectID: "project-1")
    )

    #expect(request.kind == .command)
    #expect(request.command == "curl https://example.com")
    #expect(request.cwd == "/tmp/project")
    #expect(request.network == "example.com · https")
    #expect(request.providerRequestID == .integer(42))
}

@MainActor
@Test func hostedSessionsOwnIndependentClientsAndStopIndividually() async throws {
    let factory = FakeHostedProviderFactory()
    let api = FakeHostedAgentAPI()
    let coordinator = HostedAgentCoordinator(
        makeCodexClient: { factory.make() }, api: api
    )

    let first = try await coordinator.createAndAssign(
        provider: .codex, cwd: FileManager.default.temporaryDirectory.path,
        projectID: "project-1",
        roleID: "role-1", sendOnboarding: true,
        configuration: HostedAgentConfiguration(
            model: "gpt-5.6-sol", reasoningEffort: "high"
        )
    )
    let second = try await coordinator.createAndAssign(
        provider: .codex, cwd: FileManager.default.temporaryDirectory.path,
        projectID: "project-1",
        roleID: "role-2", sendOnboarding: false
    )

    #expect(coordinator.sessions.count == 2)
    #expect(first.principalID != second.principalID)
    #expect(first.providerSessionID == "thread-1")
    #expect(second.providerSessionID == "thread-2")
    #expect(first.model == "gpt-5.6-sol")
    #expect(first.reasoningEffort == "high")
    #expect(await factory.client(at: 0).startedConfigurations == [
        HostedAgentConfiguration(model: "gpt-5.6-sol", reasoningEffort: "high")
    ])
    let connected = await api.connected
    let assignments = await api.assignments
    #expect(connected == [first.principalID, second.principalID])
    #expect(assignments.map(\.roleID) == ["role-1", "role-2"])

    await coordinator.stop(first)

    #expect(coordinator.sessions == [second])
    let disconnected = await api.disconnected
    let firstStopped = await factory.client(at: 0).stopped
    let secondStopped = await factory.client(at: 1).stopped
    #expect(disconnected == [first.principalID])
    #expect(firstStopped)
    #expect(!secondStopped)

    await coordinator.stop(second)
}

@MainActor
@Test func persistedHostedSessionsResumeWithoutStartingNewThreads() async throws {
    let factory = FakeHostedProviderFactory()
    let api = FakeHostedAgentAPI()
    let workspace = FileManager.default.temporaryDirectory
    let valid = HostedAgentRecoveryRecord(
        principalID: "agent-hosted-restored", localName: "codex-hosted-restored",
        provider: .codex, providerSessionID: "thread-existing",
        cwd: workspace.path, projectID: "project-1",
        model: "gpt-5.6-sol", reasoningEffort: "xhigh"
    )
    let missingWorkspace = HostedAgentRecoveryRecord(
        principalID: "agent-hosted-missing", localName: "codex-hosted-missing",
        provider: .codex, providerSessionID: "thread-missing",
        cwd: workspace.appending(path: UUID().uuidString).path, projectID: "project-1"
    )
    await api.setRecoveryRecords([missingWorkspace, valid])
    let coordinator = HostedAgentCoordinator(
        makeCodexClient: { factory.make() }, api: api
    )

    try await coordinator.restorePersistedSessions()

    #expect(coordinator.sessions == [HostedAgentSession(
        principalID: valid.principalID, localName: valid.localName,
        provider: valid.provider, providerSessionID: valid.providerSessionID,
        cwd: workspace.path, projectID: "project-1",
        model: "gpt-5.6-sol", reasoningEffort: "xhigh"
    )])
    #expect(coordinator.recoveryFailures[missingWorkspace.principalID] != nil)
    #expect(factory.count == 1)
    #expect(await factory.client(at: 0).startedCwds.isEmpty)
    let resumed = await factory.client(at: 0).resumedThreads
    #expect(resumed.count == 1)
    #expect(resumed.first?.0 == "thread-existing")
    #expect(resumed.first?.1 == workspace.path)
    #expect(await factory.client(at: 0).resumedConfigurations == [
        HostedAgentConfiguration(model: "gpt-5.6-sol", reasoningEffort: "xhigh")
    ])
    #expect(await factory.client(at: 0).identity == HostedAgentIdentity(
        principalID: valid.principalID, projectID: "project-1"
    ))
    #expect(await api.connected == [valid.principalID])
    #expect(await api.assignments.isEmpty)

    try await coordinator.restorePersistedSessions()
    #expect(factory.count == 1)
    await coordinator.stop(coordinator.sessions[0])
    await coordinator.stopFailedRecovery(missingWorkspace.principalID)
    #expect(coordinator.recoveryFailures.isEmpty)
    #expect(await api.disconnectForgetValues == [true, true])
}

@MainActor
@Test func hostedTurnStreamsAndInterruptLeavesInboxUnprocessed() async throws {
    let client = StreamingFakeHostedProviderClient()
    let api = FakeHostedAgentAPI()
    await api.setInboxMessages([
        HostedInboxMessage(
            seq: 41, projectSeq: 7, senderID: "pm-1", body: "긴 작업"
        )
    ])
    let coordinator = HostedAgentCoordinator(makeCodexClient: { client }, api: api)
    let session = try await coordinator.createAndAssign(
        provider: .codex, cwd: FileManager.default.temporaryDirectory.path,
        projectID: "project-1", roleID: "role-1", sendOnboarding: false
    )
    for _ in 0..<100 where coordinator.activeTurns[session.id]?.turnID == nil {
        try await Task.sleep(for: .milliseconds(10))
    }

    let progress = try #require(coordinator.activeTurns[session.id])
    #expect(progress.turnID == "turn-stream")
    #expect(progress.text == "부분 응답")
    #expect(progress.activities == [HostedAgentActivity(
        id: "tool-1", title: "Command", detail: "swift test", state: .succeeded
    )])
    await coordinator.interruptTurn(session)
    for _ in 0..<100 where coordinator.turnFailures[session.id] == nil {
        try await Task.sleep(for: .milliseconds(10))
    }

    #expect(coordinator.activeTurns[session.id] == nil)
    #expect(coordinator.turnFailures[session.id]?.contains("중단") == true)
    #expect(await client.interrupted?.threadID == "thread-stream")
    #expect(await client.interrupted?.turnID == "turn-stream")
    #expect(await api.replies.isEmpty)
    #expect(await api.ackedThrough.isEmpty)
    coordinator.retryTurn(session)
    for _ in 0..<100 where coordinator.activeTurns[session.id]?.turnID == nil {
        try await Task.sleep(for: .milliseconds(10))
    }
    #expect(coordinator.activeTurns[session.id]?.text == "부분 응답")
    await coordinator.stop(session)
    #expect(coordinator.activeTurns[session.id] == nil)
    #expect(coordinator.turnFailures[session.id] == nil)
}

@MainActor
@Test func hostedApprovalWaitsForPMAndReturnsSessionDecision() async throws {
    let factory = FakeHostedProviderFactory()
    let api = FakeHostedAgentAPI()
    let coordinator = HostedAgentCoordinator(makeCodexClient: { factory.make() }, api: api)
    coordinator.approvalTimeoutMinutes = 0
    let session = try await coordinator.createAndAssign(
        provider: .codex, cwd: FileManager.default.temporaryDirectory.path,
        projectID: "project-1",
        roleID: "role-1", sendOnboarding: false
    )
    let approval = try HostedApprovalRequest.decode(
        method: "item/fileChange/requestApproval", requestID: .integer(7),
        params: [
            "threadId": session.providerSessionID, "turnId": "turn-1",
            "itemId": "item-1", "grantRoot": "/outside/project",
        ],
        identity: HostedAgentIdentity(
            principalID: session.principalID, projectID: session.projectID
        )
    )
    let decisionTask = Task {
        await factory.client(at: 0).askForApproval(approval)
    }
    for _ in 0..<100 where coordinator.pendingApprovals.isEmpty {
        try await Task.sleep(for: .milliseconds(10))
    }

    let pending = try #require(coordinator.pendingApprovals.first)
    #expect(pending.path == "/outside/project")
    #expect(pending.auditID == "audit-1")
    coordinator.resolve(pending, as: .allowSession)
    #expect(await decisionTask.value == .allowSession)
    #expect(coordinator.pendingApprovals.isEmpty)
    await coordinator.stop(session)
}

@MainActor
@Test func crashedHostedProcessGoesOfflineAndCancelsItsApproval() async throws {
    let factory = FakeHostedProviderFactory()
    let api = FakeHostedAgentAPI()
    let coordinator = HostedAgentCoordinator(makeCodexClient: { factory.make() }, api: api)
    coordinator.approvalTimeoutMinutes = 0
    let session = try await coordinator.createAndAssign(
        provider: .codex, cwd: FileManager.default.temporaryDirectory.path,
        projectID: "project-1", roleID: "role-1", sendOnboarding: false
    )
    let approval = try HostedApprovalRequest.decode(
        method: "item/commandExecution/requestApproval", requestID: .string("approval-1"),
        params: [
            "threadId": session.providerSessionID, "turnId": "turn-1",
            "itemId": "item-1", "command": ["curl", "example.com"],
        ],
        identity: HostedAgentIdentity(
            principalID: session.principalID, projectID: session.projectID
        )
    )
    let decisionTask = Task {
        await factory.client(at: 0).askForApproval(approval)
    }
    for _ in 0..<100 where coordinator.pendingApprovals.isEmpty {
        try await Task.sleep(for: .milliseconds(10))
    }
    #expect(coordinator.pendingApprovals.count == 1)

    await factory.client(at: 0).crash()
    for _ in 0..<100 where !coordinator.sessions.isEmpty {
        try await Task.sleep(for: .milliseconds(10))
    }

    #expect(coordinator.sessions.isEmpty)
    #expect(coordinator.pendingApprovals.isEmpty)
    #expect(await decisionTask.value == .cancel)
    #expect(await api.disconnected == [session.principalID])
    #expect(await api.disconnectForgetValues == [false])
    #expect(await factory.client(at: 0).stopped)
}

@MainActor
@Test func oneHostedCrashDoesNotStopAnotherSession() async throws {
    let factory = FakeHostedProviderFactory()
    let api = FakeHostedAgentAPI()
    let coordinator = HostedAgentCoordinator(makeCodexClient: { factory.make() }, api: api)
    let first = try await coordinator.createAndAssign(
        provider: .codex, cwd: FileManager.default.temporaryDirectory.path,
        projectID: "project-1", roleID: "role-1", sendOnboarding: false
    )
    let second = try await coordinator.createAndAssign(
        provider: .codex, cwd: FileManager.default.temporaryDirectory.path,
        projectID: "project-1", roleID: "role-2", sendOnboarding: false
    )

    await factory.client(at: 0).crash()
    for _ in 0..<100 where coordinator.sessions.count == 2 {
        try await Task.sleep(for: .milliseconds(10))
    }

    #expect(coordinator.sessions == [second])
    #expect(await api.disconnected == [first.principalID])
    #expect(!(await factory.client(at: 1).stopped))
    await coordinator.stop(second)
}
