import AppKit
import Foundation

enum HostedAgentProviderID: String, CaseIterable, Codable, Identifiable, Sendable {
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
    case turnInterrupted

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
        case .turnInterrupted:
            "Codex turn이 중단되었습니다. 원본 inbox 메시지는 처리되지 않은 채 남아 있습니다."
        }
    }
}

enum HostedAgentTurnEvent: Equatable, Sendable {
    case started(turnID: String)
    case delta(String)
    case activity(HostedAgentActivity)
    case interruptRejected(String)
}

enum HostedAgentActivityState: Equatable, Sendable {
    case running, succeeded, failed
}

struct HostedAgentActivity: Identifiable, Equatable, Sendable {
    let id: String
    let title: String
    let detail: String?
    let state: HostedAgentActivityState

    static func decode(item: [String: Any], completed: Bool) -> HostedAgentActivity? {
        guard let id = item["id"] as? String, let type = item["type"] as? String else {
            return nil
        }
        let title: String
        let detail: String?
        switch type {
        case "commandExecution":
            title = "Command"
            detail = item["command"] as? String
        case "fileChange":
            title = "File changes"
            let paths = (item["changes"] as? [[String: Any]])?
                .compactMap { $0["path"] as? String }
            detail = paths?.isEmpty == false ? paths?.joined(separator: ", ") : nil
        case "mcpToolCall":
            let server = item["server"] as? String
            let tool = item["tool"] as? String
            title = [server, tool].compactMap { $0 }.joined(separator: " · ")
            detail = compactJSON(item["arguments"])
        case "dynamicToolCall":
            title = item["tool"] as? String ?? "Tool"
            detail = compactJSON(item["arguments"])
        case "webSearch":
            title = "Web search"
            detail = item["query"] as? String
        case "collabAgentToolCall":
            title = "Agent · \(item["tool"] as? String ?? "collaboration")"
            detail = item["prompt"] as? String
        case "subAgentActivity":
            title = "Sub-agent"
            detail = item["agentPath"] as? String
        case "imageView":
            title = "View image"
            detail = item["path"] as? String
        default:
            return nil
        }
        let status = (item["status"] as? String)?.lowercased()
        let state: HostedAgentActivityState
        if ["failed", "declined", "rejected", "error"].contains(status) {
            state = .failed
        } else if completed {
            state = .succeeded
        } else {
            state = .running
        }
        return HostedAgentActivity(
            id: id, title: title.isEmpty ? "Tool" : title,
            detail: detail.map { String($0.prefix(500)) }, state: state
        )
    }

    private static func compactJSON(_ value: Any?) -> String? {
        guard let value, JSONSerialization.isValidJSONObject(value),
              let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
              let text = String(data: data, encoding: .utf8)
        else { return value as? String }
        return text
    }
}

struct HostedReasoningOption: Identifiable, Equatable, Sendable {
    let effort: String
    let description: String
    var id: String { effort }
}

struct HostedModelOption: Identifiable, Equatable, Sendable {
    let id: String
    let displayName: String
    let description: String
    let isDefault: Bool
    let defaultReasoningEffort: String
    let supportedReasoningEfforts: [HostedReasoningOption]

    static func decode(_ value: [String: Any]) throws -> HostedModelOption {
        guard let id = (value["model"] as? String) ?? (value["id"] as? String),
              let displayName = value["displayName"] as? String,
              let description = value["description"] as? String,
              let isDefault = value["isDefault"] as? Bool,
              let defaultEffort = value["defaultReasoningEffort"] as? String,
              let rawEfforts = value["supportedReasoningEfforts"] as? [[String: Any]]
        else { throw HostedAgentError.invalidResponse }
        let efforts = try rawEfforts.map { option in
            guard let effort = option["reasoningEffort"] as? String,
                  let description = option["description"] as? String
            else { throw HostedAgentError.invalidResponse }
            return HostedReasoningOption(effort: effort, description: description)
        }
        guard !efforts.isEmpty, efforts.contains(where: { $0.effort == defaultEffort }) else {
            throw HostedAgentError.invalidResponse
        }
        return HostedModelOption(
            id: id, displayName: displayName, description: description,
            isDefault: isDefault, defaultReasoningEffort: defaultEffort,
            supportedReasoningEfforts: efforts
        )
    }
}

struct HostedAgentConfiguration: Equatable, Sendable {
    let model: String
    let reasoningEffort: String
}

typealias HostedTurnEventHandler = @Sendable (HostedAgentTurnEvent) async -> Void

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
    func models() async throws -> [HostedModelOption]
    func startThread(cwd: String) async throws -> String
    func startThread(cwd: String, configuration: HostedAgentConfiguration?) async throws -> String
    func resumeThread(threadID: String, cwd: String) async throws -> String
    func resumeThread(
        threadID: String, cwd: String, configuration: HostedAgentConfiguration?
    ) async throws -> String
    func runTurn(
        threadID: String, text: String, onEvent: HostedTurnEventHandler?
    ) async throws -> String
    func interruptTurn(threadID: String, turnID: String) async throws
    func waitForExit() async
    func stop() async
}

extension HostedAgentProviderClient {
    func models() async throws -> [HostedModelOption] { [] }
    func startThread(
        cwd: String, configuration: HostedAgentConfiguration?
    ) async throws -> String {
        try await startThread(cwd: cwd)
    }
    func resumeThread(
        threadID: String, cwd: String, configuration: HostedAgentConfiguration?
    ) async throws -> String {
        try await resumeThread(threadID: threadID, cwd: cwd)
    }
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
    let model: String?
    let reasoningEffort: String?
    var id: String { principalID }

    init(
        principalID: String, localName: String, provider: HostedAgentProviderID,
        providerSessionID: String, cwd: String, projectID: String,
        model: String? = nil, reasoningEffort: String? = nil
    ) {
        self.principalID = principalID
        self.localName = localName
        self.provider = provider
        self.providerSessionID = providerSessionID
        self.cwd = cwd
        self.projectID = projectID
        self.model = model
        self.reasoningEffort = reasoningEffort
    }
}

struct HostedAgentRecoveryRecord: Equatable, Decodable, Sendable {
    let principalID: String
    let localName: String
    let provider: HostedAgentProviderID
    let providerSessionID: String
    let cwd: String?
    let projectID: String?
    let model: String?
    let reasoningEffort: String?

    private enum CodingKeys: String, CodingKey {
        case principalID = "principal_id"
        case localName = "local_name"
        case provider
        case providerSessionID = "session_id"
        case cwd
        case projectID = "project_id"
        case model
        case reasoningEffort = "reasoning_effort"
    }

    init(
        principalID: String, localName: String, provider: HostedAgentProviderID,
        providerSessionID: String, cwd: String?, projectID: String?,
        model: String? = nil, reasoningEffort: String? = nil
    ) {
        self.principalID = principalID
        self.localName = localName
        self.provider = provider
        self.providerSessionID = providerSessionID
        self.cwd = cwd
        self.projectID = projectID
        self.model = model
        self.reasoningEffort = reasoningEffort
    }
}

struct HostedAgentTurnProgress: Equatable, Sendable {
    let threadID: String
    var turnID: String?
    var text: String
    var activities: [HostedAgentActivity]
    var interruptRequested: Bool
    var interruptError: String?
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

// runTurn은 stdout을 동기식으로 읽는 동안 provider actor를 점유한다. interrupt를
// 같은 actor 메서드로 보내면 turn이 끝날 때까지 실행되지 않으므로, stdin write만
// lock으로 직렬화한 별도 경계에서 보낸다.
private final class HostedJSONLWriter: @unchecked Sendable {
    private let lock = NSLock()
    private var input: FileHandle?
    private var nextOutOfBandRequestID = -1

    func attach(_ input: FileHandle) {
        lock.lock()
        self.input = input
        nextOutOfBandRequestID = -1
        lock.unlock()
    }

    func detach() {
        lock.lock()
        input = nil
        lock.unlock()
    }

    func write(_ message: [String: Any]) throws {
        let data = try JSONSerialization.data(withJSONObject: message) + Data([0x0A])
        lock.lock()
        defer { lock.unlock() }
        guard let input else { throw HostedAgentError.processEnded("") }
        input.write(data)
    }

    func interrupt(threadID: String, turnID: String) throws {
        lock.lock()
        defer { lock.unlock() }
        guard let input else { throw HostedAgentError.processEnded("") }
        let requestID = nextOutOfBandRequestID
        nextOutOfBandRequestID -= 1
        let message: [String: Any] = [
            "method": "turn/interrupt", "id": requestID,
            "params": ["threadId": threadID, "turnId": turnID],
        ]
        let data = try JSONSerialization.data(withJSONObject: message) + Data([0x0A])
        input.write(data)
    }
}

private final class HostedProcessExitSignal: Sendable {
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
    private nonisolated let writer = HostedJSONLWriter()
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
        writer.attach(stdinPipe.fileHandleForWriting)
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
                ],
                "capabilities": ["experimentalApi": true],
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

    func models() async throws -> [HostedModelOption] {
        if !initialized || process?.isRunning != true { _ = try await start() }
        var models: [HostedModelOption] = []
        var cursor: String?
        repeat {
            var params: [String: Any] = ["includeHidden": false]
            if let cursor { params["cursor"] = cursor }
            let result = try request(method: "model/list", params: params)
            guard let data = result["data"] as? [[String: Any]] else {
                throw HostedAgentError.invalidResponse
            }
            models += try data.map(HostedModelOption.decode)
            cursor = result["nextCursor"] as? String
        } while cursor != nil
        return models
    }

    func startThread(
        cwd: String, configuration: HostedAgentConfiguration? = nil
    ) async throws -> String {
        if !initialized || process?.isRunning != true { _ = try await start() }
        var params: [String: Any] = [
            "cwd": cwd,
            "approvalPolicy": "untrusted",
            "sandbox": "workspace-write",
            "ephemeral": false,
        ]
        if let configuration {
            params["model"] = configuration.model
            params["config"] = ["model_reasoning_effort": configuration.reasoningEffort]
        }
        let result = try request(
            method: "thread/start",
            params: params
        )
        guard let thread = result["thread"] as? [String: Any],
              let id = thread["id"] as? String else {
            throw HostedAgentError.invalidResponse
        }
        return id
    }

    func startThread(cwd: String) async throws -> String {
        try await startThread(cwd: cwd, configuration: nil)
    }

    func resumeThread(
        threadID: String, cwd: String, configuration: HostedAgentConfiguration? = nil
    ) async throws -> String {
        if !initialized || process?.isRunning != true { _ = try await start() }
        var params: [String: Any] = [
            "threadId": threadID,
            "cwd": cwd,
            "approvalPolicy": "untrusted",
            "sandbox": "workspace-write",
        ]
        if let configuration { params["model"] = configuration.model }
        let result = try request(
            method: "thread/resume",
            params: params
        )
        guard let thread = result["thread"] as? [String: Any],
              let id = thread["id"] as? String,
              id == threadID else {
            throw HostedAgentError.invalidResponse
        }
        if let configuration {
            _ = try request(
                method: "thread/settings/update",
                params: [
                    "threadId": threadID,
                    "model": configuration.model,
                    "effort": configuration.reasoningEffort,
                ]
            )
        }
        return id
    }

    func resumeThread(threadID: String, cwd: String) async throws -> String {
        try await resumeThread(threadID: threadID, cwd: cwd, configuration: nil)
    }

    func runTurn(
        threadID: String, text: String, onEvent: HostedTurnEventHandler? = nil
    ) async throws -> String {
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
        await onEvent?(.started(turnID: turnID))
        var answer = ""
        while true {
            let message = try readMessage()
            if let responseID = message["id"] as? NSNumber,
               responseID.intValue < 0 {
                if let error = message["error"] as? [String: Any] {
                    await onEvent?(.interruptRejected(
                        error["message"] as? String ?? "Codex turn 중단 요청이 거절되었습니다."
                    ))
                }
                continue
            }
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
                await onEvent?(.delta(delta))
            }
            if (method == "item/started" || method == "item/completed"),
               params["turnId"] as? String == turnID,
               let item = params["item"] as? [String: Any],
               let activity = HostedAgentActivity.decode(
                    item: item, completed: method == "item/completed"
               ) {
                await onEvent?(.activity(activity))
            }
            if method == "turn/completed",
               let completed = params["turn"] as? [String: Any],
               completed["id"] as? String == turnID {
                let status = completed["status"] as? String
                if status == "interrupted" {
                    throw HostedAgentError.turnInterrupted
                }
                if status == "failed" {
                    let error = completed["error"] as? [String: Any]
                    throw HostedAgentError.rpc(
                        error?["message"] as? String ?? "Codex turn이 실패했습니다."
                    )
                }
                return answer.trimmingCharacters(in: .whitespacesAndNewlines)
            }
        }
    }

    nonisolated func interruptTurn(threadID: String, turnID: String) async throws {
        try writer.interrupt(threadID: threadID, turnID: turnID)
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
        guard process?.isRunning == true else {
            throw HostedAgentError.processEnded(stderrBuffer.text())
        }
        try writer.write(message)
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
        writer.detach()
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
    func recoverableHostedSessions() async throws -> [HostedAgentRecoveryRecord]
    func connectHostedSession(_ session: HostedAgentSession) async throws
    func disconnectHostedSession(_ principalID: String, forget: Bool) async throws
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
    @Published private(set) var recoveryFailures: [String: String] = [:]
    @Published private(set) var activeTurns: [String: HostedAgentTurnProgress] = [:]
    @Published private(set) var turnFailures: [String: String] = [:]
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
    private var isRestoring = false
    private var hasRestored = false
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
        roleID: String, sendOnboarding: Bool,
        configuration: HostedAgentConfiguration? = nil
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

            let threadID = try await client.startThread(cwd: cwd, configuration: configuration)
            let suffix = String(threadID.prefix(8))
            let session = HostedAgentSession(
                principalID: identity.principalID,
                localName: "codex-hosted-\(suffix)", provider: .codex,
                providerSessionID: threadID, cwd: cwd, projectID: projectID,
                model: configuration?.model, reasoningEffort: configuration?.reasoningEffort
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

    func availableModels(provider: HostedAgentProviderID) async throws -> [HostedModelOption] {
        guard provider == .codex else {
            throw HostedAgentError.rpc("Claude Code hosted session은 아직 지원하지 않습니다.")
        }
        let client = makeCodexClient()
        do {
            var account = try await client.start()
            if !account.authentication.isChatGPT {
                creationState = .waitingForLogin
                let url = try await client.beginChatGPTLogin()
                NSWorkspace.shared.open(url)
                for _ in 0..<60 {
                    try await Task.sleep(for: .seconds(2))
                    account = try await client.account()
                    if account.authentication.isChatGPT { break }
                }
                guard account.authentication.isChatGPT else {
                    throw HostedAgentError.rpc("ChatGPT 로그인을 확인하지 못했습니다.")
                }
            }
            let models = try await client.models()
            guard !models.isEmpty else {
                throw HostedAgentError.rpc("선택 가능한 Codex 모델이 없습니다.")
            }
            await client.stop()
            creationState = .stopped
            return models
        } catch {
            await client.stop()
            creationState = .stopped
            throw error
        }
    }

    func restorePersistedSessions() async throws {
        guard !hasRestored, !isRestoring else { return }
        isRestoring = true
        defer { isRestoring = false }

        let records = try await api.recoverableHostedSessions()
        for record in records {
            guard clients[record.principalID] == nil,
                  !sessions.contains(where: { $0.id == record.principalID }) else { continue }
            guard record.provider == .codex else {
                recoveryFailures[record.principalID] = "지원하지 않는 hosted provider입니다."
                continue
            }
            guard let cwd = HostedWorkspaceDirectory.validatedPath(record.cwd) else {
                recoveryFailures[record.principalID] =
                    "저장된 workspace 폴더가 없거나 읽을 수 없습니다."
                continue
            }
            guard let projectID = record.projectID, !projectID.isEmpty else {
                recoveryFailures[record.principalID] = "저장된 프로젝트가 없습니다."
                continue
            }

            let session = HostedAgentSession(
                principalID: record.principalID, localName: record.localName,
                provider: record.provider, providerSessionID: record.providerSessionID,
                cwd: cwd, projectID: projectID,
                model: record.model, reasoningEffort: record.reasoningEffort
            )
            let client = makeCodexClient()
            do {
                try await client.configure(
                    identity: HostedAgentIdentity(
                        principalID: session.principalID, projectID: session.projectID
                    )
                )
                await client.configureApprovalHandler { [weak self] approval in
                    guard let self else { return .cancel }
                    return await self.waitForApproval(approval)
                }
                let account = try await client.start()
                guard account.authentication.isChatGPT else {
                    throw HostedAgentError.rpc(
                        "저장된 Codex session을 복구하려면 ChatGPT 로그인이 필요합니다."
                    )
                }
                let configuration = session.model.flatMap { model in
                    session.reasoningEffort.map {
                        HostedAgentConfiguration(model: model, reasoningEffort: $0)
                    }
                }
                _ = try await client.resumeThread(
                    threadID: session.providerSessionID, cwd: session.cwd,
                    configuration: configuration
                )
                try await api.connectHostedSession(session)
                clients[session.id] = client
                sessions.append(session)
                recoveryFailures.removeValue(forKey: session.id)
                watchLifecycle(session: session, client: client)
                startInbox(session: session, projectID: session.projectID, client: client)
            } catch {
                await client.stop()
                recoveryFailures[session.id] = error.localizedDescription
            }
        }
        hasRestored = true
    }

    func stop(_ session: HostedAgentSession) async {
        lifecycleTasks.removeValue(forKey: session.id)?.cancel()
        cancelApprovals(for: session.principalID)
        inboxTasks.removeValue(forKey: session.id)?.cancel()
        activeTurns.removeValue(forKey: session.id)
        turnFailures.removeValue(forKey: session.id)
        try? await api.disconnectHostedSession(session.principalID, forget: true)
        if let client = clients.removeValue(forKey: session.id) {
            await client.stop()
        }
        sessions.removeAll { $0.id == session.id }
    }

    func stopFailedRecovery(_ principalID: String) async {
        do {
            try await api.disconnectHostedSession(principalID, forget: true)
            recoveryFailures.removeValue(forKey: principalID)
        } catch {
            recoveryFailures[principalID] = error.localizedDescription
        }
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
        activeTurns.removeValue(forKey: session.id)
        turnFailures.removeValue(forKey: session.id)
        cancelApprovals(for: session.principalID)
        clients.removeValue(forKey: session.id)
        sessions.removeAll { $0.id == session.id }
        await client.stop()

        // local node가 app-server와 동시에 흔들려도 binding을 online으로 남기지
        // 않는다. 앱이 살아 있는 동안 재시도하고, 앱 종료는 task를 함께 끝낸다.
        while !Task.isCancelled {
            do {
                try await api.disconnectHostedSession(session.principalID, forget: false)
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

    func interruptTurn(_ session: HostedAgentSession) async {
        guard let client = clients[session.id],
              var progress = activeTurns[session.id],
              let turnID = progress.turnID,
              !progress.interruptRequested else { return }
        // 승인 응답을 기다리는 중이면 runTurn이 provider stdout을 다시 읽지 못한다.
        // 먼저 해당 session의 승인 waiter를 cancel해 read loop를 풀어 준다.
        cancelApprovals(for: session.principalID)
        progress.interruptRequested = true
        progress.interruptError = nil
        activeTurns[session.id] = progress
        do {
            try await client.interruptTurn(
                threadID: progress.threadID, turnID: turnID
            )
        } catch {
            progress.interruptRequested = false
            progress.interruptError = error.localizedDescription
            activeTurns[session.id] = progress
        }
    }

    func retryTurn(_ session: HostedAgentSession) {
        guard let client = clients[session.id] else { return }
        turnFailures.removeValue(forKey: session.id)
        startInbox(session: session, projectID: session.projectID, client: client)
    }

    private func applyTurnEvent(
        _ event: HostedAgentTurnEvent, session: HostedAgentSession
    ) {
        guard var progress = activeTurns[session.id] else { return }
        switch event {
        case let .started(turnID):
            progress.turnID = turnID
        case let .delta(delta):
            progress.text += delta
        case let .activity(activity):
            if let index = progress.activities.firstIndex(where: { $0.id == activity.id }) {
                progress.activities[index] = activity
            } else {
                progress.activities.append(activity)
                if progress.activities.count > 12 { progress.activities.removeFirst() }
            }
        case let .interruptRejected(message):
            progress.interruptRequested = false
            progress.interruptError = message
        }
        activeTurns[session.id] = progress
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
                            self.activeTurns[session.id] = HostedAgentTurnProgress(
                                threadID: session.providerSessionID, turnID: nil,
                                text: "", activities: [],
                                interruptRequested: false, interruptError: nil
                            )
                            do {
                                answer = try await client.runTurn(
                                    threadID: session.providerSessionID, text: message.body
                                ) { [weak self] event in
                                    await self?.applyTurnEvent(event, session: session)
                                }
                            } catch {
                                self.activeTurns.removeValue(forKey: session.id)
                                if Task.isCancelled { return }
                                self.turnFailures[session.id] = error.localizedDescription
                                return
                            }
                            self.activeTurns.removeValue(forKey: session.id)
                            self.turnFailures.removeValue(forKey: session.id)
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
