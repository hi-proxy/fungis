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
    private var approvalHandler: HostedApprovalHandler?

    init(threadID: String) { self.threadID = threadID }

    func configure(identity: HostedAgentIdentity) async throws { self.identity = identity }
    func configureApprovalHandler(_ handler: @escaping HostedApprovalHandler) async {
        approvalHandler = handler
    }
    func start() async throws -> HostedAgentAccount { accountValue }
    func account() async throws -> HostedAgentAccount { accountValue }
    func beginChatGPTLogin() async throws -> URL { URL(string: "https://example.com/login")! }
    func startThread(cwd: String) async throws -> String { threadID }
    func runTurn(threadID: String, text: String) async throws -> String { "" }
    func stop() async { stopped = true }

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
}

private actor FakeHostedAgentAPI: HostedAgentAPIClient {
    private(set) var connected: [String] = []
    private(set) var disconnected: [String] = []
    private(set) var assignments: [(roleID: String, agentID: String)] = []
    private(set) var createdApprovals: [HostedApprovalRequest] = []
    private(set) var resolvedApprovals: [(String, String, String?)] = []

    func assignRole(id: String, agentID: String, sendOnboarding: Bool) async throws {
        assignments.append((id, agentID))
    }

    func connectHostedSession(_ session: HostedAgentSession) async throws {
        connected.append(session.principalID)
    }

    func disconnectHostedSession(_ principalID: String) async throws {
        disconnected.append(principalID)
    }

    func hostedInbox(principalID: String, after: Int) async throws -> [HostedInboxMessage] { [] }

    func replyFromHosted(
        principalID: String, projectID: String, recipientID: String,
        body: String, inReplyToProjectSeq: Int
    ) async throws {}

    func ackHosted(principalID: String, through: Int) async throws {}

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
        provider: .codex, cwd: "/tmp/project", projectID: "project-1",
        roleID: "role-1", sendOnboarding: true
    )
    let second = try await coordinator.createAndAssign(
        provider: .codex, cwd: "/tmp/project", projectID: "project-1",
        roleID: "role-2", sendOnboarding: false
    )

    #expect(coordinator.sessions.count == 2)
    #expect(first.principalID != second.principalID)
    #expect(first.providerSessionID == "thread-1")
    #expect(second.providerSessionID == "thread-2")
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
@Test func hostedApprovalWaitsForPMAndReturnsSessionDecision() async throws {
    let factory = FakeHostedProviderFactory()
    let api = FakeHostedAgentAPI()
    let coordinator = HostedAgentCoordinator(makeCodexClient: { factory.make() }, api: api)
    coordinator.approvalTimeoutMinutes = 0
    let session = try await coordinator.createAndAssign(
        provider: .codex, cwd: "/tmp/project", projectID: "project-1",
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
