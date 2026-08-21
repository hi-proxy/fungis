import AppKit
import Foundation

enum HostedAgentProviderID: String, CaseIterable, Identifiable, Sendable {
    case codex
    case claudeCode = "claude-code"

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .codex: "Codex"
        case .claudeCode: "Claude Code"
        }
    }

    var isAvailable: Bool { self == .codex }

    var detail: String {
        switch self {
        case .codex:
            "ChatGPT 구독으로 로그인하고 app-server를 앱이 관리합니다."
        case .claudeCode:
            "같은 hosted agent 경계에 추후 추가합니다."
        }
    }
}

enum HostedAuthentication: Equatable, Sendable {
    case signedOut
    case chatGPT(plan: String?, email: String?)
    case apiKey
    case other(String)

    var title: String {
        switch self {
        case .signedOut: "로그인 필요"
        case let .chatGPT(plan, _):
            plan.map { "ChatGPT · \($0.capitalized)" } ?? "ChatGPT"
        case .apiKey: "API key"
        case let .other(mode): mode
        }
    }

    var isChatGPT: Bool {
        if case .chatGPT = self { return true }
        return false
    }
}

struct HostedAgentAccount: Equatable, Sendable {
    let authentication: HostedAuthentication
    let requiresOpenAIAuthentication: Bool

    static func decode(_ result: [String: Any]) -> HostedAgentAccount {
        let requiresAuthentication = result["requiresOpenaiAuth"] as? Bool ?? true
        guard let account = result["account"] as? [String: Any],
              let type = account["type"] as? String
        else {
            return HostedAgentAccount(
                authentication: .signedOut,
                requiresOpenAIAuthentication: requiresAuthentication
            )
        }

        let authentication: HostedAuthentication
        switch type.lowercased() {
        case "chatgpt":
            authentication = .chatGPT(
                plan: account["planType"] as? String,
                email: account["email"] as? String
            )
        case "apikey", "api_key":
            authentication = .apiKey
        default:
            authentication = .other(type)
        }
        return HostedAgentAccount(
            authentication: authentication,
            requiresOpenAIAuthentication: requiresAuthentication
        )
    }
}

enum HostedAgentConnectionState: Equatable {
    case stopped
    case starting
    case ready(HostedAgentAccount)
    case waitingForLogin
    case failed(String)

    var title: String {
        switch self {
        case .stopped: "시작하지 않음"
        case .starting: "app-server 시작 중"
        case let .ready(account): account.authentication.title
        case .waitingForLogin: "ChatGPT 로그인 대기 중"
        case .failed: "연결 실패"
        }
    }

    var isRunning: Bool {
        switch self {
        case .ready, .waitingForLogin: true
        case .stopped, .starting, .failed: false
        }
    }
}

enum HostedAgentError: LocalizedError, Sendable {
    case executableNotFound
    case processEnded(String)
    case invalidResponse
    case rpc(String)
    case missingLoginURL
    case invalidWorkspace

    var errorDescription: String? {
        switch self {
        case .executableNotFound:
            "Codex 실행 파일을 찾지 못했습니다. Codex CLI를 먼저 설치하세요."
        case let .processEnded(detail):
            detail.isEmpty ? "Codex app-server가 종료되었습니다." : detail
        case .invalidResponse:
            "Codex app-server가 올바르지 않은 응답을 보냈습니다."
        case let .rpc(message): message
        case .missingLoginURL:
            "Codex app-server가 로그인 주소를 보내지 않았습니다."
        case .invalidWorkspace:
            "Hosted session에 사용할 유효한 workspace 폴더를 선택하세요."
        }
    }
}

enum HostedWorkspaceDirectory {
    static func validatedPath(
        _ path: String?, fileManager: FileManager = .default
    ) -> String? {
        guard let path else { return nil }
        let expanded = NSString(string: path).expandingTildeInPath
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !expanded.isEmpty else { return nil }
        let standardized = URL(fileURLWithPath: expanded).standardizedFileURL.path
        guard standardized != "/" else { return nil }
        var isDirectory: ObjCBool = false
        guard fileManager.fileExists(atPath: standardized, isDirectory: &isDirectory),
              isDirectory.boolValue,
              fileManager.isReadableFile(atPath: standardized)
        else { return nil }
        return standardized
    }
}

struct HostedExecutableResolver {
    static func codexURL(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        fileManager: FileManager = .default
    ) -> URL? {
        var candidates: [String] = []
        if let explicit = environment["FUNGIS_CODEX_PATH"], !explicit.isEmpty {
            candidates.append(explicit)
        }
        if let path = environment["PATH"] {
            candidates += path.split(separator: ":").map { "\($0)/codex" }
        }
        candidates += [
            NSString(string: "~/.local/bin/codex").expandingTildeInPath,
            "/opt/homebrew/bin/codex",
            "/usr/local/bin/codex",
        ]

        var seen = Set<String>()
        return candidates.first { path in
            seen.insert(path).inserted && fileManager.isExecutableFile(atPath: path)
        }.map { URL(fileURLWithPath: $0) }
    }
}

protocol HostedAgentProviderClient: Sendable {
    func configure(identity: HostedAgentIdentity) async throws
    func configureApprovalHandler(_ handler: @escaping HostedApprovalHandler) async
    func start() async throws -> HostedAgentAccount
    func account() async throws -> HostedAgentAccount
    func beginChatGPTLogin() async throws -> URL
    func startThread(cwd: String) async throws -> String
    func runTurn(threadID: String, text: String) async throws -> String
    func waitForExit() async
    func stop() async
}

typealias HostedApprovalHandler = @Sendable (HostedApprovalRequest) async -> HostedApprovalDecision

enum HostedApprovalKind: String, Sendable {
    case command, fileChange, permissions

    var title: String {
        switch self {
        case .command: "명령 실행"
        case .fileChange: "파일 변경"
        case .permissions: "추가 권한"
        }
    }
}

enum HostedApprovalDecision: String, Sendable {
    case allowOnce, allowSession, deny, cancel

    var wireDecision: String {
        switch self {
        case .allowOnce: "accept"
        case .allowSession: "acceptForSession"
        case .deny: "decline"
        case .cancel: "cancel"
        }
    }

    var status: String { self == .allowOnce || self == .allowSession ? "allowed" : "denied" }
    var scope: String? { self == .allowSession ? "session" : self == .allowOnce ? "turn" : nil }
}

struct HostedApprovalRequest: Identifiable, Equatable, Sendable {
    let id: String
    let providerRequestID: HostedProviderRequestID
    let principalID: String
    let projectID: String
    let kind: HostedApprovalKind
    let method: String
    let threadID: String?
    let turnID: String?
    let itemID: String?
    let reason: String?
    let command: String?
    let cwd: String?
    let path: String?
    let network: String?
    let detailJSON: String
    let requestedPermissionsJSON: String?
    let availableDecisions: [String]
    var auditID: String?

    var sessionLabel: String { String(principalID.suffix(8)) }

    static func decode(
        method: String, requestID: HostedProviderRequestID, params: [String: Any],
        identity: HostedAgentIdentity
    ) throws -> HostedApprovalRequest {
        let kind: HostedApprovalKind
        switch method {
        case "item/commandExecution/requestApproval": kind = .command
        case "item/fileChange/requestApproval": kind = .fileChange
        case "item/permissions/requestApproval": kind = .permissions
        default: throw HostedAgentError.invalidResponse
        }
        let data = try JSONSerialization.data(withJSONObject: params, options: [.sortedKeys])
        let detail = String(data: data, encoding: .utf8) ?? "{}"
        let command: String?
        if let parts = params["command"] as? [String] {
            command = parts.joined(separator: " ")
        } else {
            command = params["command"] as? String
        }
        var network: String?
        if let context = params["networkApprovalContext"] as? [String: Any] {
            network = [context["host"], context["protocol"]]
                .compactMap { $0 as? String }.joined(separator: " · ")
            if network?.isEmpty == true { network = nil }
        }
        let permissionsJSON: String?
        if let permissions = params["permissions"] {
            let value = try JSONSerialization.data(withJSONObject: permissions, options: [.sortedKeys])
            permissionsJSON = String(data: value, encoding: .utf8)
        } else {
            permissionsJSON = nil
        }
        return HostedApprovalRequest(
            id: UUID().uuidString, providerRequestID: requestID,
            principalID: identity.principalID, projectID: identity.projectID,
            kind: kind, method: method,
            threadID: params["threadId"] as? String,
            turnID: params["turnId"] as? String,
            itemID: params["itemId"] as? String,
            reason: params["reason"] as? String, command: command,
            cwd: params["cwd"] as? String, path: params["grantRoot"] as? String,
            network: network,
            detailJSON: detail, requestedPermissionsJSON: permissionsJSON,
            availableDecisions: params["availableDecisions"] as? [String] ?? [],
            auditID: nil
        )
    }
}

enum HostedProviderRequestID: Equatable, Sendable {
    case integer(Int)
    case string(String)

    var auditValue: String {
        switch self {
        case let .integer(value): String(value)
        case let .string(value): value
        }
    }
}

struct HostedAgentIdentity: Equatable, Sendable {
    let principalID: String
    let projectID: String
}

struct HostedAgentSession: Identifiable, Equatable, Sendable {
    let principalID: String
    let localName: String
    let provider: HostedAgentProviderID
    let providerSessionID: String
    let cwd: String
    let projectID: String
    var id: String { principalID }
}

private final class TextBuffer: @unchecked Sendable {
    private let lock = NSLock()
    private var value = ""

    func append(_ data: Data) {
        guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
        lock.lock()
        value.append(text)
        if value.count > 4_000 { value = String(value.suffix(4_000)) }
        lock.unlock()
    }

    func text() -> String {
        lock.lock()
        defer { lock.unlock() }
        return value.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

private final class HostedProcessExitSignal: @unchecked Sendable {
    let stream: AsyncStream<Void>
    private let continuation: AsyncStream<Void>.Continuation

    init() {
        let pair = AsyncStream<Void>.makeStream()
        stream = pair.stream
        continuation = pair.continuation
    }

    func finish() {
        continuation.yield(())
        continuation.finish()
    }
}

actor CodexAppServerClient: HostedAgentProviderClient {
    private let executableURL: URL?
    private var process: Process?
    private var input: FileHandle?
    private var output: FileHandle?
    private var errorOutput: FileHandle?
    private var outputBuffer = Data()
    private var nextRequestID = 1
    private var initialized = false
    private let stderrBuffer = TextBuffer()
    private var identity: HostedAgentIdentity?
    private var approvalHandler: HostedApprovalHandler?
    private var exitSignal: HostedProcessExitSignal?

    init(executableURL: URL? = HostedExecutableResolver.codexURL()) {
        self.executableURL = executableURL
    }

    func configure(identity: HostedAgentIdentity) async throws {
        if process?.isRunning == true, self.identity != identity {
            throw HostedAgentError.rpc(
                "실행 중인 Codex app-server의 hosted identity는 바꿀 수 없습니다."
            )
        }
        self.identity = identity
    }

    func configureApprovalHandler(_ handler: @escaping HostedApprovalHandler) async {
        approvalHandler = handler
    }

    func start() async throws -> HostedAgentAccount {
        if initialized, process?.isRunning == true {
            return try readAccount()
        }
        stopProcess()
        guard let executableURL else { throw HostedAgentError.executableNotFound }

        let process = Process()
        let stdinPipe = Pipe()
        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        process.executableURL = executableURL
        process.arguments = ["app-server"]
        if let identity {
            var environment = ProcessInfo.processInfo.environment
            environment["FUNGIS_HOSTED_PRINCIPAL_ID"] = identity.principalID
            environment["FUNGIS_HOSTED_PROJECT_ID"] = identity.projectID
            process.environment = environment
            process.arguments? += [
                "-c",
                "shell_environment_policy.set.FUNGIS_HOSTED_PRINCIPAL_ID=\"\(identity.principalID)\"",
                "-c",
                "shell_environment_policy.set.FUNGIS_HOSTED_PROJECT_ID=\"\(identity.projectID)\"",
            ]
        }
        process.standardInput = stdinPipe
        process.standardOutput = stdoutPipe
        process.standardError = stderrPipe
        stderrPipe.fileHandleForReading.readabilityHandler = { [stderrBuffer] handle in
            stderrBuffer.append(handle.availableData)
        }
        let exitSignal = HostedProcessExitSignal()
        process.terminationHandler = { _ in exitSignal.finish() }
        do {
            try process.run()
        } catch {
            exitSignal.finish()
            throw error
        }

        self.process = process
        self.exitSignal = exitSignal
        input = stdinPipe.fileHandleForWriting
        output = stdoutPipe.fileHandleForReading
        errorOutput = stderrPipe.fileHandleForReading
        outputBuffer.removeAll(keepingCapacity: true)
        nextRequestID = 1

        _ = try request(
            method: "initialize",
            params: [
                "clientInfo": [
                    "name": "fungis_mac",
                    "title": "Fungis",
                    "version": "0.1.0",
                ]
            ]
        )
        try notify(method: "initialized", params: [:])
        initialized = true
        return try readAccount()
    }

    func account() async throws -> HostedAgentAccount {
        guard initialized, process?.isRunning == true else {
            return try await start()
        }
        return try readAccount()
    }

    func beginChatGPTLogin() async throws -> URL {
        if !initialized || process?.isRunning != true { _ = try await start() }
        let result = try request(
            method: "account/login/start",
            params: [
                "type": "chatgpt",
                "useHostedLoginSuccessPage": true,
                "appBrand": "codex",
            ]
        )
        guard let value = result["authUrl"] as? String, let url = URL(string: value) else {
            throw HostedAgentError.missingLoginURL
        }
        return url
    }

    func startThread(cwd: String) async throws -> String {
        if !initialized || process?.isRunning != true { _ = try await start() }
        let result = try request(
            method: "thread/start",
            params: [
                "cwd": cwd,
                "approvalPolicy": "untrusted",
                "sandbox": "workspace-write",
                "ephemeral": false,
            ]
        )
        guard let thread = result["thread"] as? [String: Any],
              let id = thread["id"] as? String else {
            throw HostedAgentError.invalidResponse
        }
        return id
    }

    func runTurn(threadID: String, text: String) async throws -> String {
        let result = try request(
            method: "turn/start",
            params: [
                "threadId": threadID,
                "input": [["type": "text", "text": text]],
            ]
        )
        guard let turn = result["turn"] as? [String: Any],
              let turnID = turn["id"] as? String else {
            throw HostedAgentError.invalidResponse
        }
        var answer = ""
        while true {
            let message = try readMessage()
            if let method = message["method"] as? String,
               method.hasSuffix("/requestApproval"),
               let params = message["params"] as? [String: Any] {
                let requestID: HostedProviderRequestID
                if let value = message["id"] as? NSNumber {
                    requestID = .integer(value.intValue)
                } else if let value = message["id"] as? String {
                    requestID = .string(value)
                } else {
                    throw HostedAgentError.invalidResponse
                }
                try await answerApproval(method: method, requestID: requestID, params: params)
                continue
            }
            guard let method = message["method"] as? String,
                  let params = message["params"] as? [String: Any] else { continue }
            if method == "item/agentMessage/delta",
               params["turnId"] as? String == turnID,
               let delta = params["delta"] as? String {
                answer += delta
            }
            if method == "turn/completed",
               let completed = params["turn"] as? [String: Any],
               completed["id"] as? String == turnID {
                if completed["status"] as? String == "failed" {
                    let error = completed["error"] as? [String: Any]
                    throw HostedAgentError.rpc(
                        error?["message"] as? String ?? "Codex turn이 실패했습니다."
                    )
                }
                return answer.trimmingCharacters(in: .whitespacesAndNewlines)
            }
        }
    }

    private func answerApproval(
        method: String, requestID: HostedProviderRequestID, params: [String: Any]
    ) async throws {
        guard let identity else { throw HostedAgentError.invalidResponse }
        let request = try HostedApprovalRequest.decode(
            method: method, requestID: requestID, params: params, identity: identity
        )
        let decision = await approvalHandler?(request) ?? .deny
        let result: [String: Any]
        if request.kind == .permissions {
            let permissions: Any
            if decision == .allowOnce || decision == .allowSession,
               let json = request.requestedPermissionsJSON,
               let data = json.data(using: .utf8) {
                permissions = try JSONSerialization.jsonObject(with: data)
            } else {
                permissions = [String: Any]()
            }
            result = [
                "permissions": permissions,
                "scope": decision == .allowSession ? "session" : "turn",
            ]
        } else {
            result = ["decision": decision.wireDecision]
        }
        let wireID: Any
        switch requestID {
        case let .integer(value): wireID = value
        case let .string(value): wireID = value
        }
        try write(["id": wireID, "result": result])
    }

    func stop() async {
        stopProcess()
    }

    func waitForExit() async {
        guard let exitSignal else { return }
        for await _ in exitSignal.stream { return }
    }

    private func readAccount() throws -> HostedAgentAccount {
        let result = try request(
            method: "account/read", params: ["refreshToken": false]
        )
        return HostedAgentAccount.decode(result)
    }

    private func request(method: String, params: [String: Any]) throws -> [String: Any] {
        let requestID = nextRequestID
        nextRequestID += 1
        try write(["method": method, "id": requestID, "params": params])

        while true {
            let message = try readMessage()
            guard (message["id"] as? NSNumber)?.intValue == requestID else { continue }
            if let error = message["error"] as? [String: Any] {
                throw HostedAgentError.rpc(
                    error["message"] as? String ?? "Codex app-server 요청이 실패했습니다."
                )
            }
            guard let result = message["result"] as? [String: Any] else {
                throw HostedAgentError.invalidResponse
            }
            return result
        }
    }

    private func notify(method: String, params: [String: Any]) throws {
        try write(["method": method, "params": params])
    }

    private func write(_ message: [String: Any]) throws {
        guard let input, process?.isRunning == true else {
            throw HostedAgentError.processEnded(stderrBuffer.text())
        }
        let data = try JSONSerialization.data(withJSONObject: message)
        input.write(data + Data([0x0A]))
    }

    private func readMessage() throws -> [String: Any] {
        while true {
            if let newline = outputBuffer.firstIndex(of: 0x0A) {
                let line = outputBuffer[..<newline]
                outputBuffer.removeSubrange(...newline)
                guard !line.isEmpty else { continue }
                guard let value = try JSONSerialization.jsonObject(with: Data(line))
                    as? [String: Any]
                else { throw HostedAgentError.invalidResponse }
                return value
            }
            guard let output else { throw HostedAgentError.processEnded(stderrBuffer.text()) }
            // readData(ofLength:) may wait for the full byte count while app-server
            // keeps stdout open. availableData returns as soon as one JSONL frame has
            // bytes, which is the framing contract we need here.
            let chunk = output.availableData
            guard !chunk.isEmpty else {
                throw HostedAgentError.processEnded(stderrBuffer.text())
            }
            outputBuffer.append(chunk)
        }
    }

    private func stopProcess() {
        initialized = false
        input?.closeFile()
        input = nil
        output = nil
        errorOutput?.readabilityHandler = nil
        errorOutput = nil
        if let process, process.isRunning { process.terminate() }
        self.process = nil
        exitSignal = nil
    }
}

protocol HostedAgentAPIClient: Sendable {
    func assignRole(id: String, agentID: String, sendOnboarding: Bool) async throws
    func connectHostedSession(_ session: HostedAgentSession) async throws
    func disconnectHostedSession(_ principalID: String) async throws
    func hostedInbox(principalID: String, after: Int) async throws -> [HostedInboxMessage]
    func replyFromHosted(
        principalID: String, projectID: String, recipientID: String,
        body: String, inReplyToProjectSeq: Int
    ) async throws
    func ackHosted(principalID: String, through: Int) async throws
    func createHostedPermission(_ approval: HostedApprovalRequest) async throws -> String
    func resolvePermission(
        requestID: String, projectID: String, status: String,
        decision: String?, decisionScope: String?
    ) async throws
}

extension FungisAPI: HostedAgentAPIClient {}

@MainActor
final class HostedAgentCoordinator: ObservableObject {
    @Published private(set) var creationState: HostedAgentConnectionState = .stopped
    @Published private(set) var sessions: [HostedAgentSession] = []
    @Published private(set) var pendingApprovals: [HostedApprovalRequest] = []
    @Published private(set) var presentedApproval: HostedApprovalRequest?
    @Published var approvalTimeoutMinutes: Int {
        didSet { UserDefaults.standard.set(approvalTimeoutMinutes, forKey: Self.timeoutKey) }
    }
    private let makeCodexClient: @Sendable () -> any HostedAgentProviderClient
    private var clients: [String: any HostedAgentProviderClient] = [:]
    private var inboxTasks: [String: Task<Void, Never>] = [:]
    private var lifecycleTasks: [String: Task<Void, Never>] = [:]
    private var approvalWaiters: [
        String: CheckedContinuation<HostedApprovalDecision, Never>
    ] = [:]
    private var approvalTimeoutTasks: [String: Task<Void, Never>] = [:]
    private let api: any HostedAgentAPIClient
    private static let timeoutKey = "hostedApprovalTimeoutMinutes"

    init(
        makeCodexClient: @escaping @Sendable () -> any HostedAgentProviderClient = {
            CodexAppServerClient()
        },
        api: any HostedAgentAPIClient = FungisAPI()
    ) {
        self.makeCodexClient = makeCodexClient
        self.api = api
        approvalTimeoutMinutes = UserDefaults.standard.integer(forKey: Self.timeoutKey)
    }

    func createAndAssign(
        provider: HostedAgentProviderID, cwd: String, projectID: String,
        roleID: String, sendOnboarding: Bool
    ) async throws -> HostedAgentSession {
        guard provider == .codex else {
            throw HostedAgentError.rpc("Claude Code hosted session은 아직 지원하지 않습니다.")
        }
        guard let cwd = HostedWorkspaceDirectory.validatedPath(cwd) else {
            throw HostedAgentError.invalidWorkspace
        }

        creationState = .starting
        let identity = HostedAgentIdentity(
            principalID: "agent-hosted-codex-\(UUID().uuidString.lowercased())",
            projectID: projectID
        )
        let client = makeCodexClient()
        do {
            try await client.configure(identity: identity)
            await client.configureApprovalHandler { [weak self] approval in
                guard let self else { return .cancel }
                return await self.waitForApproval(approval)
            }
            var account = try await client.start()
            if !account.authentication.isChatGPT {
                creationState = .waitingForLogin
                let url = try await client.beginChatGPTLogin()
                NSWorkspace.shared.open(url)
                var authenticated: HostedAgentAccount?
                for _ in 0..<60 {
                    try await Task.sleep(for: .seconds(2))
                    account = try await client.account()
                    if account.authentication.isChatGPT {
                        authenticated = account
                        break
                    }
                }
                guard let authenticated else {
                    throw HostedAgentError.rpc("ChatGPT 로그인을 확인하지 못했습니다.")
                }
                account = authenticated
            }
            creationState = .ready(account)

            let threadID = try await client.startThread(cwd: cwd)
            let suffix = String(threadID.prefix(8))
            let session = HostedAgentSession(
                principalID: identity.principalID,
                localName: "codex-hosted-\(suffix)", provider: .codex,
                providerSessionID: threadID, cwd: cwd, projectID: projectID
            )
            try await api.connectHostedSession(session)
            clients[session.id] = client
            sessions.append(session)
            watchLifecycle(session: session, client: client)

            try await assign(
                session: session, roleID: roleID, projectID: projectID,
                sendOnboarding: sendOnboarding
            )
            creationState = .stopped
            return session
        } catch is CancellationError {
            if clients[identity.principalID] == nil {
                await client.stop()
            }
            creationState = .stopped
            throw CancellationError()
        } catch {
            if clients[identity.principalID] == nil {
                await client.stop()
            }
            creationState = .failed(error.localizedDescription)
            throw error
        }
    }

    func stop(_ session: HostedAgentSession) async {
        lifecycleTasks.removeValue(forKey: session.id)?.cancel()
        cancelApprovals(for: session.principalID)
        inboxTasks.removeValue(forKey: session.id)?.cancel()
        try? await api.disconnectHostedSession(session.principalID)
        if let client = clients.removeValue(forKey: session.id) {
            await client.stop()
        }
        sessions.removeAll { $0.id == session.id }
    }

    private func watchLifecycle(
        session: HostedAgentSession, client: any HostedAgentProviderClient
    ) {
        lifecycleTasks[session.id]?.cancel()
        lifecycleTasks[session.id] = Task { [weak self] in
            await client.waitForExit()
            guard !Task.isCancelled, let self else { return }
            await self.providerExited(session, client: client)
        }
    }

    private func providerExited(
        _ session: HostedAgentSession, client: any HostedAgentProviderClient
    ) async {
        guard sessions.contains(where: { $0.id == session.id }) else { return }
        inboxTasks.removeValue(forKey: session.id)?.cancel()
        cancelApprovals(for: session.principalID)
        clients.removeValue(forKey: session.id)
        sessions.removeAll { $0.id == session.id }
        await client.stop()

        // local node가 app-server와 동시에 흔들려도 binding을 online으로 남기지
        // 않는다. 앱이 살아 있는 동안 재시도하고, 앱 종료는 task를 함께 끝낸다.
        while !Task.isCancelled {
            do {
                try await api.disconnectHostedSession(session.principalID)
                break
            } catch {
                try? await Task.sleep(for: .seconds(1))
            }
        }
        lifecycleTasks.removeValue(forKey: session.id)
    }

    func showNextApproval() {
        if presentedApproval == nil { presentedApproval = pendingApprovals.first }
    }

    func postponePresentedApproval() {
        presentedApproval = nil
    }

    func resolve(_ approval: HostedApprovalRequest, as decision: HostedApprovalDecision) {
        guard pendingApprovals.contains(where: { $0.id == approval.id }) else { return }
        approvalTimeoutTasks.removeValue(forKey: approval.id)?.cancel()
        pendingApprovals.removeAll { $0.id == approval.id }
        if presentedApproval?.id == approval.id { presentedApproval = nil }
        approvalWaiters.removeValue(forKey: approval.id)?.resume(returning: decision)
        if let auditID = approval.auditID {
            Task { [api] in
                try? await api.resolvePermission(
                    requestID: auditID, projectID: approval.projectID,
                    status: decision.status, decision: decision.rawValue,
                    decisionScope: decision.scope
                )
            }
        }
        showNextApproval()
    }

    private func waitForApproval(
        _ incoming: HostedApprovalRequest
    ) async -> HostedApprovalDecision {
        var approval = incoming
        approval.auditID = try? await api.createHostedPermission(incoming)
        return await withCheckedContinuation { continuation in
            approvalWaiters[approval.id] = continuation
            pendingApprovals.append(approval)
            if presentedApproval == nil { presentedApproval = approval }
            if approvalTimeoutMinutes > 0 {
                let minutes = approvalTimeoutMinutes
                approvalTimeoutTasks[approval.id] = Task { [weak self] in
                    try? await Task.sleep(for: .seconds(minutes * 60))
                    guard !Task.isCancelled else { return }
                    self?.resolve(approval, as: .deny)
                }
            }
        }
    }

    private func cancelApprovals(for principalID: String) {
        for approval in pendingApprovals where approval.principalID == principalID {
            resolve(approval, as: .cancel)
        }
    }

    func assign(
        session: HostedAgentSession, roleID: String, projectID: String,
        sendOnboarding: Bool
    ) async throws {
        guard session.projectID == projectID else {
            throw HostedAgentError.rpc("다른 프로젝트의 hosted session은 배정할 수 없습니다.")
        }
        guard let client = clients[session.id] else {
            throw HostedAgentError.rpc("hosted session process가 실행 중이 아닙니다.")
        }
        try await api.assignRole(
            id: roleID, agentID: session.principalID, sendOnboarding: sendOnboarding
        )
        startInbox(session: session, projectID: projectID, client: client)
    }

    private func startInbox(
        session: HostedAgentSession, projectID: String,
        client: any HostedAgentProviderClient
    ) {
        inboxTasks[session.id]?.cancel()
        inboxTasks[session.id] = Task { [weak self] in
            var after = 0
            var pendingAnswers: [Int: String] = [:]
            while !Task.isCancelled {
                guard let self else { return }
                do {
                    let messages = try await self.api.hostedInbox(
                        principalID: session.principalID, after: after
                    )
                    for message in messages {
                        let answer: String
                        if let pending = pendingAnswers[message.seq] {
                            answer = pending
                        } else {
                            answer = try await client.runTurn(
                                threadID: session.providerSessionID, text: message.body
                            )
                            pendingAnswers[message.seq] = answer
                        }
                        if !answer.isEmpty {
                            try await self.api.replyFromHosted(
                                principalID: session.principalID, projectID: projectID,
                                recipientID: message.senderID, body: answer,
                                inReplyToProjectSeq: message.projectSeq
                            )
                        }
                        try await self.api.ackHosted(
                            principalID: session.principalID, through: message.seq
                        )
                        pendingAnswers.removeValue(forKey: message.seq)
                        after = max(after, message.seq)
                    }
                } catch {
                    // The binding remains attached so the role can show SESSION OFFLINE if
                    // the provider process dies. A later explicit Stop owns cleanup.
                }
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }
}
