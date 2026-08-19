import Foundation

struct FungisSnapshot: Decodable {
    var projectID: String
    var projects: [FungisProject]
    var projectRepositories: [ProjectRepository]
    var pmID: String
    var pmProfile: PMProfile
    var targets: [Target]
    var statuses: [AgentStatus]
    var timeline: [ChatMessage]
    var attention: [AttentionRequest]
    var bookmarks: [MessageBookmark]
    var timelinePins: [TimelinePin]
    var shared: [SharedValue]
    var work: [WorkItem]
    var roles: [WorkspaceRole]
    var agents: [AgentTerminal]
    var permissionRequests: [PermissionRequest] = []

    static let empty = FungisSnapshot(
        projectID: "local", projects: [], projectRepositories: [],
        pmID: "", pmProfile: .empty,
        targets: [], statuses: [], timeline: [], attention: [], bookmarks: [],
        timelinePins: [],
        shared: [], work: [], roles: [], agents: [], permissionRequests: []
    )

    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case projects
        case projectRepositories = "project_repositories"
        case pmID = "pm_id"
        case pmProfile = "pm_profile"
        case targets, statuses, timeline, attention, bookmarks, shared, work, roles, agents
        case timelinePins = "timeline_pins"
        case permissionRequests = "permission_requests"
    }
}

struct FungisProject: Decodable, Identifiable, Hashable {
    var id: String
    var name: String
    var createdAt: String
    /// 방에 마지막으로 들어온 메시지 seq. 메시지가 없으면 nil이다.
    var lastMessageSeq: Int?
    /// "hq"면 상황보드를 가진 방이다. 목록에서 맨 위에 둔다.
    ///
    /// 옵셔널인 이유는 서버가 앱보다 낡을 수 있어서다. 필수로 받으면 칸
    /// 하나가 없다고 스냅샷 전체가 안 읽히고 앱이 빈 화면이 된다.
    var kind: String?
    var isHQ: Bool { kind == "hq" }
    enum CodingKeys: String, CodingKey {
        case id, name, kind
        case createdAt = "created_at"
        case lastMessageSeq = "last_message_seq"
    }
}

struct ProjectRepository: Decodable, Identifiable {
    var projectID: String
    var path: String
    var updatedAt: String
    var git: GitContext?
    var id: String { projectID }
    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case path, git
        case updatedAt = "updated_at"
    }
}

struct PMProfile: Decodable {
    var principalID: String
    var displayName: String
    var hasAvatar: Bool
    var avatarUpdatedAt: String?
    static let empty = PMProfile(
        principalID: "", displayName: "PM", hasAvatar: false, avatarUpdatedAt: nil
    )
    enum CodingKeys: String, CodingKey {
        case principalID = "principal_id"
        case displayName = "display_name"
        case hasAvatar = "has_avatar"
        case avatarUpdatedAt = "avatar_updated_at"
    }
}

struct AgentMembership: Decodable, Hashable, Identifiable {
    var agentID: String
    var roleID: String
    var roleName: String
    var projectID: String
    var projectName: String
    var assignedAt: String
    var id: String { "\(projectID):\(roleID)" }
    enum CodingKeys: String, CodingKey {
        case agentID = "agent_id"
        case roleID = "role_id"
        case roleName = "role_name"
        case projectID = "project_id"
        case projectName = "project_name"
        case assignedAt = "assigned_at"
    }
}

struct Target: Decodable, Identifiable, Hashable {
    var localName: String
    var principalID: String
    var nickname: String?
    var provider: String
    var lifecycle: String
    var memberships: [AgentMembership]
    var id: String { localName }
    var displayName: String { nickname?.isEmpty == false ? nickname! : localName }

    enum CodingKeys: String, CodingKey {
        case localName = "local_name"
        case principalID = "principal_id"
        case nickname, provider, lifecycle, memberships
    }
}

struct AgentStatus: Decodable, Identifiable {
    var id: String
    var provider: String
    var lifecycle: String
    var localPending: Int
    var processedSeq: Int

    enum CodingKeys: String, CodingKey {
        case id, provider, lifecycle
        case localPending = "local_pending"
        case processedSeq = "processed_seq"
    }
}

struct MessageRecipient: Decodable {
    var recipientID: String
    var displayName: String
    var receivedAt: String?
    var processedAt: String?

    enum CodingKeys: String, CodingKey {
        case recipientID = "recipient_id"
        case displayName = "display_name"
        case receivedAt = "received_at"
        case processedAt = "processed_at"
    }
}

struct MessageReference: Decodable {
    var principalID: String
    var displayName: String

    enum CodingKeys: String, CodingKey {
        case principalID = "principal_id"
        case displayName = "display_name"
    }
}

struct RoleRecipient: Decodable {
    var roleID: String
    var name: String
    var deliveredAgentID: String?
    var deliveredAt: String?

    enum CodingKeys: String, CodingKey {
        case roleID = "role_id"
        case name
        case deliveredAgentID = "delivered_agent_id"
        case deliveredAt = "delivered_at"
    }
}

struct ChatMessage: Decodable, Identifiable {
    /// 저장과 정렬에 쓰는 전역 번호. 화면에는 방별 번호를 쓴다.
    var seq: Int
    /// 방마다 1부터 세는 표시 번호. 에이전트가 보는 번호와 같다.
    var projectSeq: Int?
    var senderID: String
    var senderName: String
    var body: String
    var createdAt: String
    var recipients: [MessageRecipient]
    var references: [MessageReference]
    var pmRelation: String?
    var track: String?
    var tags: [String]
    var inReplyTo: Int?
    /// 답장이 가리키는 부모의 방별 번호. 화면에는 이 값을 쓴다.
    var inReplyToProjectSeq: Int?
    var detectedContexts: [DetectedContext]
    var roleRecipients: [RoleRecipient]
    var id: Int { seq }
    /// 화면과 대화에서 부르는 번호. 에이전트가 보는 것과 같다.
    var displaySeq: Int { projectSeq ?? seq }

    enum CodingKeys: String, CodingKey {
        case seq, body, recipients, references, track, tags
        case projectSeq = "project_seq"
        case createdAt = "created_at"
        case detectedContexts = "detected_contexts"
        case roleRecipients = "role_recipients"
        case inReplyTo = "in_reply_to"
        case inReplyToProjectSeq = "in_reply_to_project_seq"
        case pmRelation = "pm_relation"
        case senderID = "sender_id"
        case senderName = "sender_name"
    }
}

struct AttentionRequest: Decodable, Identifiable {
    var seq: Int
    var senderID: String
    var senderName: String
    var replyLevel: String
    var body: String
    var pmRelation: String?
    var track: String?
    var tags: [String]
    var detectedContexts: [DetectedContext]
    var id: Int { seq }

    enum CodingKeys: String, CodingKey {
        case seq, body, track, tags
        case detectedContexts = "detected_contexts"
        case pmRelation = "pm_relation"
        case senderID = "sender_id"
        case senderName = "sender_name"
        case replyLevel = "reply_level"
    }
}

struct MessageBookmark: Decodable, Identifiable {
    /// 화면에 부르는 방별 번호. 저장은 전역 seq 그대로다.
    var messageProjectSeq: Int?
    var id: String
    var messageSeq: Int

    var label: String
    var createdBy: String
    var createdByName: String
    var createdAt: String

    enum CodingKeys: String, CodingKey {
        case id, label
        case messageSeq = "message_seq"
        case messageProjectSeq = "message_project_seq"
        case createdBy = "created_by"
        case createdByName = "created_by_name"
        case createdAt = "created_at"
    }

    var displaySeq: Int { messageProjectSeq ?? messageSeq }
}

struct TimelinePin: Decodable, Identifiable {
    /// 화면에 부르는 방별 번호. 저장은 전역 seq 그대로다.
    var afterMessageProjectSeq: Int?
    var id: String
    var afterMessageSeq: Int
    var label: String
    var createdBy: String
    var createdByName: String
    var createdAt: String

    enum CodingKeys: String, CodingKey {
        case id, label
        case afterMessageSeq = "after_message_seq"
        case afterMessageProjectSeq = "after_message_project_seq"
        case createdBy = "created_by"
        case createdByName = "created_by_name"
        case createdAt = "created_at"
    }

    var displaySeq: Int { afterMessageProjectSeq ?? afterMessageSeq }
}

struct SharedValue: Decodable, Identifiable {
    var key: String
    var value: String
    var version: Int
    var id: String { key }
}

struct WorkItem: Decodable, Identifiable {
    var id: String
    var agentName: String
    var title: String
    var status: String
    var elapsedSeconds: Int
    var lastReport: String?
    var tokenUsage: Int?

    enum CodingKeys: String, CodingKey {
        case id, title, status
        case agentName = "agent_name"
        case elapsedSeconds = "elapsed_seconds"
        case lastReport = "last_report"
        case tokenUsage = "token_usage"
    }
}

struct WorkspaceRole: Decodable, Identifiable {
    var id: String
    var workspaceID: String
    var name: String
    var onboardingPrompt: String
    var assigned: Bool
    var assignmentID: String?
    var agentID: String?
    var agentName: String?
    var assignedAt: String?
    var onboardingSent: Bool
    var sessionConnected: Bool
    var hasAvatar: Bool
    var avatarUpdatedAt: String?

    enum CodingKeys: String, CodingKey {
        case id, name, assigned
        case workspaceID = "workspace_id"
        case onboardingPrompt = "onboarding_prompt"
        case assignmentID = "assignment_id"
        case agentID = "agent_id"
        case agentName = "agent_name"
        case assignedAt = "assigned_at"
        case onboardingSent = "onboarding_sent"
        case sessionConnected = "session_connected"
        case hasAvatar = "has_avatar"
        case avatarUpdatedAt = "avatar_updated_at"
    }
}

struct RoleAssignment: Decodable, Identifiable {
    var id: String
    var roleID: String
    var agentID: String
    var agentName: String
    var assignedAt: String
    var endedAt: String?
    var onboardingSent: Bool

    enum CodingKeys: String, CodingKey {
        case id
        case roleID = "role_id"
        case agentID = "agent_id"
        case agentName = "agent_name"
        case assignedAt = "assigned_at"
        case endedAt = "ended_at"
        case onboardingSent = "onboarding_sent"
    }
}

struct AgentTerminal: Decodable, Identifiable {
    var provider: String
    var agentSessionID: String
    var surfaceID: String
    var title: String
    var cwd: String?
    var lifecycle: String
    var bindingVerified: Bool
    var connected: Bool
    /// 빈 프롬프트가 아닌 무언가에서 멈춰 있다. 권한 확인이나 선택 화면이다.
    var awaitingInput: Bool = false
    var localName: String?
    var nickname: String?
    var principalID: String?
    var memberships: [AgentMembership]
    var git: GitContext?
    var id: String { "\(provider):\(agentSessionID)" }

    enum CodingKeys: String, CodingKey {
        case provider, title, cwd, lifecycle, connected, nickname, memberships
        case principalID = "principal_id"
        case awaitingInput = "awaiting_input"
        case agentSessionID = "agent_session_id"
        case surfaceID = "surface_id"
        case bindingVerified = "binding_verified"
        case localName = "local_name"
    }
}

struct GitContext: Decodable {
    var repoRoot: String
    var worktree: String
    var commonDir: String?
    var branch: String?
    var branches: [String]
    var head: String?
    var dirty: Bool
    var verified: Bool

    enum CodingKeys: String, CodingKey {
        case repoRoot = "repo_root"
        case worktree
        case commonDir = "common_dir"
        case branch, branches, head, dirty, verified
    }
}

struct DetectedContext: Decodable, Hashable {
    var kind: String
    var value: String
    var verified: Bool
}

enum DeliveryState: String {
    case incoming, sent, received, processed
}

extension ChatMessage {
    func deliveryState(pmID: String) -> DeliveryState {
        guard senderID == pmID else { return .incoming }
        guard !recipients.isEmpty else { return .sent }
        if recipients.allSatisfy({ $0.processedAt != nil }) { return .processed }
        if recipients.allSatisfy({ $0.receivedAt != nil }) { return .received }
        return .sent
    }
}


/// 에이전트가 터미널에서 권한 확인을 받는 중이다. 무엇을 하려는지까지 hook이
/// 알려주므로 화면을 읽지 않고도 그대로 보여줄 수 있다.
struct PermissionRequest: Decodable, Identifiable {
    var id: String
    var sessionID: String
    var agentID: String?
    var agentName: String?
    var toolName: String
    var toolInput: String
    var createdAt: String

    /// tool_input은 JSON 문자열로 온다. 읽을 수 있게 풀어 준다.
    var summary: String {
        guard let data = toolInput.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data)
        else { return toolInput }
        if let dictionary = object as? [String: Any] {
            let parts = dictionary
                .sorted { $0.key < $1.key }
                .map { "\($0.key): \($0.value)" }
            return parts.joined(separator: "\n")
        }
        return toolInput
    }

    enum CodingKeys: String, CodingKey {
        case id
        case sessionID = "session_id"
        case agentID = "agent_id"
        case agentName = "agent_name"
        case toolName = "tool_name"
        case toolInput = "tool_input"
        case createdAt = "created_at"
    }
}

// MARK: - 상황보드
//
// 방 스냅샷과 따로 받는다. 보드는 방에 속하지 않고, 스냅샷에 넣으면 보드가
// 한 글자 바뀔 때마다 열려 있는 모든 방 스트림이 다시 흐른다.

struct BoardNode: Decodable, Identifiable, Hashable {
    let id: String
    let projectID: String
    let title: String
    /// 방 안에서 1부터. 서버가 낡으면 안 온다.
    let number: Int?
    /// 이 티켓을 기다리는 것들. 없으면 끝내고 알릴 상대가 없다는 뜻이다.
    let blocks: [String]?
    /// 저장된 값. todo / active / done
    let status: String
    /// 선행에서 읽은 값. waiting이 여기 더 있다
    let state: String
    let blockedBy: [String]

    enum CodingKeys: String, CodingKey {
        case id, title, status, state, number, blocks
        case projectID = "project_id"
        case blockedBy = "blocked_by"
    }
}

struct BoardTrack: Decodable, Identifiable, Hashable {
    var id: String { projectID }
    let projectID: String
    let projectName: String
    /// 티켓 이름의 앞부분. 서버가 낡으면 안 온다.
    let ticketPrefix: String?
    let nodes: [BoardNode]

    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case projectName = "project_name"
        case ticketPrefix = "ticket_prefix"
        case nodes
    }
}

/// 소집 화면이 한 줄에 필요한 것. 부를 수 있나까지 같이 온다.
struct BoardRole: Decodable, Identifiable, Hashable {
    let id: String
    let name: String
    /// 담당자 principal id. HQ 타임라인이 발신자를 lead 와 맞춰볼 때 쓴다.
    let agentID: String?
    let agentName: String?

    enum CodingKeys: String, CodingKey {
        case id, name
        case agentID = "agent_id"
        case agentName = "agent_name"
    }
}

struct BoardCandidate: Decodable, Identifiable, Hashable {
    let id: String
    let name: String
    let connected: Bool
    let lead: BoardRole?
    let roles: [BoardRole]

    var canJoin: Bool { lead != nil }
}

struct BoardSnapshot: Decodable {
    let hq: FungisProject?
    let tracks: [BoardTrack]
    /// 없어도 되게 둔다. 노드가 앱보다 낡으면 이 칸이 안 온다.
    let candidates: [BoardCandidate]?

    static let empty = BoardSnapshot(hq: nil, tracks: [], candidates: nil)
}
