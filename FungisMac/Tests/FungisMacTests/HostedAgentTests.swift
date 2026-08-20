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

private actor FakeHostedProviderClient: HostedAgentProviderClient {
    let threadID: String
    private(set) var identity: HostedAgentIdentity?
    private(set) var stopped = false

    init(threadID: String) { self.threadID = threadID }

    func configure(identity: HostedAgentIdentity) async throws { self.identity = identity }
    func start() async throws -> HostedAgentAccount { accountValue }
    func account() async throws -> HostedAgentAccount { accountValue }
    func beginChatGPTLogin() async throws -> URL { URL(string: "https://example.com/login")! }
    func startThread(cwd: String) async throws -> String { threadID }
    func runTurn(threadID: String, text: String) async throws -> String { "" }
    func stop() async { stopped = true }

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
