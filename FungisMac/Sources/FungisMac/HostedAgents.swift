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
        }
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
    func start() async throws -> HostedAgentAccount
    func account() async throws -> HostedAgentAccount
    func beginChatGPTLogin() async throws -> URL
    func startThread(cwd: String) async throws -> String
    func runTurn(threadID: String, text: String) async throws -> String
    func stop() async
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
        try process.run()

        self.process = process
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
                "approvalPolicy": "never",
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

    func stop() async {
        stopProcess()
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
    }
}

@MainActor
final class HostedAgentCoordinator: ObservableObject {
    @Published private(set) var codexState: HostedAgentConnectionState = .stopped
    @Published private(set) var sessions: [HostedAgentSession] = []
    private let codex: any HostedAgentProviderClient
    private var loginPollingTask: Task<Void, Never>?
    private var inboxTasks: [String: Task<Void, Never>] = [:]
    private let api = FungisAPI()
    private var pendingIdentity: HostedAgentIdentity?

    init(codex: any HostedAgentProviderClient = CodexAppServerClient()) {
        self.codex = codex
    }

    func startCodex(cwd: String, projectID: String) async {
        codexState = .starting
        do {
            if let existing = sessions.first, existing.projectID != projectID {
                throw HostedAgentError.rpc(
                    "다른 프로젝트의 hosted Codex가 실행 중입니다. 먼저 Stop 하세요."
                )
            }
            if let pendingIdentity, pendingIdentity.projectID != projectID {
                throw HostedAgentError.rpc(
                    "다른 프로젝트의 hosted Codex가 준비 중입니다. 먼저 Stop 하세요."
                )
            }
            let identity = pendingIdentity ?? sessions.first.map {
                HostedAgentIdentity(principalID: $0.principalID, projectID: $0.projectID)
            } ?? HostedAgentIdentity(
                principalID: "agent-hosted-codex-\(UUID().uuidString.lowercased())",
                projectID: projectID
            )
            pendingIdentity = identity
            try await codex.configure(identity: identity)
            let account = try await codex.start()
            codexState = .ready(account)
            if account.authentication.isChatGPT {
                try await prepareCodexSession(cwd: cwd, identity: identity)
            }
        } catch {
            codexState = .failed(error.localizedDescription)
        }
    }

    func refreshCodexAccount(cwd: String? = nil, projectID: String? = nil) async {
        do {
            let account = try await codex.account()
            codexState = .ready(account)
            if account.authentication.isChatGPT, let cwd, let projectID {
                let identity = pendingIdentity ?? sessions.first.map {
                    HostedAgentIdentity(principalID: $0.principalID, projectID: $0.projectID)
                } ?? HostedAgentIdentity(
                    principalID: "agent-hosted-codex-\(UUID().uuidString.lowercased())",
                    projectID: projectID
                )
                pendingIdentity = identity
                try await codex.configure(identity: identity)
                try await prepareCodexSession(cwd: cwd, identity: identity)
            }
        } catch {
            codexState = .failed(error.localizedDescription)
        }
    }

    func beginCodexLogin(cwd: String, projectID: String) async {
        do {
            let url = try await codex.beginChatGPTLogin()
            codexState = .waitingForLogin
            NSWorkspace.shared.open(url)
            loginPollingTask?.cancel()
            loginPollingTask = Task { [weak self] in
                for _ in 0..<60 {
                    guard !Task.isCancelled else { return }
                    try? await Task.sleep(for: .seconds(2))
                    guard let self else { return }
                    do {
                        let account = try await self.codex.account()
                        if account.authentication.isChatGPT {
                            self.codexState = .ready(account)
                            guard let identity = self.pendingIdentity ?? self.sessions.first.map({
                                HostedAgentIdentity(
                                    principalID: $0.principalID, projectID: $0.projectID
                                )
                            }) else {
                                throw HostedAgentError.rpc("hosted identity가 없습니다.")
                            }
                            try await self.prepareCodexSession(cwd: cwd, identity: identity)
                            return
                        }
                    } catch {
                        self.codexState = .failed(error.localizedDescription)
                        return
                    }
                }
                self?.codexState = .failed("ChatGPT 로그인을 확인하지 못했습니다.")
            }
        } catch {
            codexState = .failed(error.localizedDescription)
        }
    }

    func stopCodex() async {
        loginPollingTask?.cancel()
        loginPollingTask = nil
        await codex.stop()
        for task in inboxTasks.values { task.cancel() }
        inboxTasks.removeAll()
        for session in sessions { try? await api.disconnectHostedSession(session.principalID) }
        sessions.removeAll()
        pendingIdentity = nil
        codexState = .stopped
    }

    func assign(
        session: HostedAgentSession, roleID: String, projectID: String,
        sendOnboarding: Bool
    ) async throws {
        try await api.assignRole(
            id: roleID, agentID: session.principalID, sendOnboarding: sendOnboarding
        )
        startInbox(session: session, projectID: projectID)
    }

    private func prepareCodexSession(
        cwd: String, identity: HostedAgentIdentity
    ) async throws {
        if sessions.contains(where: { $0.provider == .codex && $0.cwd == cwd }) { return }
        if !sessions.isEmpty {
            throw HostedAgentError.rpc("Codex app-server 하나에는 hosted session 하나만 둡니다.")
        }
        let threadID = try await codex.startThread(cwd: cwd)
        let suffix = String(threadID.prefix(8))
        let session = HostedAgentSession(
            principalID: identity.principalID,
            localName: "codex-hosted-\(suffix)", provider: .codex,
            providerSessionID: threadID, cwd: cwd, projectID: identity.projectID
        )
        try await api.connectHostedSession(session)
        sessions.append(session)
    }

    private func startInbox(session: HostedAgentSession, projectID: String) {
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
                            answer = try await self.codex.runTurn(
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
                    self.codexState = .failed(error.localizedDescription)
                }
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }
}
