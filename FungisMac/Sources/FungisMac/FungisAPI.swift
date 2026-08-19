import Foundation

enum FungisAPIError: LocalizedError {
    case invalidResponse
    case server(Int, String)

    var errorDescription: String? {
        switch self {
        case .invalidResponse: "Invalid response from Fungis"
        case let .server(code, detail): "Fungis \(code): \(detail)"
        }
    }
}

struct FungisAPI: Sendable {
    let baseURL: URL

    init(baseURL: URL = URL(string: "http://127.0.0.1:8790")!) {
        self.baseURL = baseURL
    }

    func resolvePermission(
        requestID: String, projectID: String, status: String
    ) async throws {
        struct Payload: Encodable { let project_id: String; let status: String }
        let _: EmptyResponse = try await request(
            "api/permission-requests/\(encoded(requestID))/resolve", method: "POST",
            body: Payload(project_id: projectID, status: status),
            acceptsAnyObject: true
        )
    }

    func state(projectID: String = "local") async throws -> FungisSnapshot {
        try await request("api/state?project_id=\(encoded(projectID))")
    }

    // MARK: 상황보드

    func board() async throws -> BoardSnapshot {
        try await request("api/board")
    }

    func connectTrack(projectID: String, hqID: String) async throws {
        struct Payload: Encodable { let hq_id: String }
        let _: EmptyResponse = try await request(
            "api/board/tracks/\(encoded(projectID))", method: "PUT",
            body: Payload(hq_id: hqID)
        )
    }

    func disconnectTrack(projectID: String) async throws {
        let _: EmptyResponse = try await request(
            "api/board/tracks/\(encoded(projectID))", method: "DELETE"
        )
    }

    func createBoardNode(projectID: String, title: String) async throws {
        struct Payload: Encodable { let project_id: String; let title: String }
        let _: EmptyResponse = try await request(
            "api/board/nodes", method: "POST",
            body: Payload(project_id: projectID, title: title)
        )
    }

    func updateBoardNode(nodeID: String, title: String? = nil, status: String? = nil) async throws {
        struct Payload: Encodable { let title: String?; let status: String? }
        let _: EmptyResponse = try await request(
            "api/board/nodes/\(encoded(nodeID))", method: "PATCH",
            body: Payload(title: title, status: status)
        )
    }

    func deleteBoardNode(nodeID: String) async throws {
        let _: EmptyResponse = try await request(
            "api/board/nodes/\(encoded(nodeID))", method: "DELETE"
        )
    }

    func linkBoardNodes(nodeID: String, waitsFor: String) async throws {
        struct Payload: Encodable { let node_id: String; let waits_for: String }
        let _: EmptyResponse = try await request(
            "api/board/edges", method: "POST",
            body: Payload(node_id: nodeID, waits_for: waitsFor)
        )
    }

    func unlinkBoardNodes(nodeID: String, waitsFor: String) async throws {
        let _: EmptyResponse = try await request(
            "api/board/edges?node_id=\(encoded(nodeID))&waits_for=\(encoded(waitsFor))",
            method: "DELETE"
        )
    }

    func setRoleLead(roleID: String, isLead: Bool) async throws {
        struct Payload: Encodable { let is_lead: Bool }
        let _: EmptyResponse = try await request(
            "api/roles/\(encoded(roleID))/lead", method: "PUT",
            body: Payload(is_lead: isLead)
        )
    }

    func flushLeadAnnouncements() async throws {
        let _: EmptyResponse = try await request(
            "api/lead-announcements/flush", method: "POST", acceptsAnyObject: true
        )
    }

    func snapshots(projectID: String = "local") -> AsyncThrowingStream<FungisSnapshot, Error> {
        AsyncThrowingStream { continuation in
            var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)!
            components.scheme = components.scheme == "https" ? "wss" : "ws"
            components.path = "/api/events"
            components.queryItems = [URLQueryItem(name: "project_id", value: projectID)]
            let task = URLSession.shared.webSocketTask(with: components.url!)
            let reader = Task {
                task.resume()
                do {
                    while !Task.isCancelled {
                        let message = try await task.receive()
                        let data: Data
                        switch message {
                        case let .data(value): data = value
                        case let .string(value): data = Data(value.utf8)
                        @unknown default: throw FungisAPIError.invalidResponse
                        }
                        continuation.yield(try JSONDecoder().decode(FungisSnapshot.self, from: data))
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in
                reader.cancel()
                task.cancel(with: .goingAway, reason: nil)
            }
        }
    }

    func history(
        projectID: String, before sequence: Int, limit: Int = 50
    ) async throws -> [ChatMessage] {
        try await request(
            "api/projects/\(encoded(projectID))/history?before=\(sequence)&limit=\(limit)"
        )
    }

    func send(
        projectID: String, recipientIDs: [String], roleIDs: [String] = [],
        referenceIDs: [String] = [],
        body: String, inReplyTo: Int? = nil,
        track: String? = nil, tags: [String]? = nil,
        inheritContext: Bool = true
    ) async throws {
        struct Payload: Encodable {
            let project_id: String
            let recipient_ids: [String]
            let role_ids: [String]
            let reference_ids: [String]
            let body: String
            let in_reply_to: Int?
            let track: String?
            let tags: [String]?
            let inherit_context: Bool
        }
        let _: EmptyResponse = try await request(
            "api/messages", method: "POST",
            body: Payload(
                project_id: projectID,
                recipient_ids: recipientIDs, role_ids: roleIDs,
                reference_ids: referenceIDs,
                body: body, in_reply_to: inReplyTo,
                track: track, tags: tags, inherit_context: inheritContext
            ),
            acceptsAnyObject: true
        )
    }

    func createBookmark(
        projectID: String, messageSeq: Int, label: String
    ) async throws {
        struct Payload: Encodable { let label: String }
        let _: EmptyResponse = try await request(
            "api/projects/\(encoded(projectID))/messages/\(messageSeq)/bookmarks",
            method: "POST", body: Payload(label: label), acceptsAnyObject: true
        )
    }

    func deleteBookmark(projectID: String, bookmarkID: String) async throws {
        let _: EmptyResponse = try await request(
            "api/projects/\(encoded(projectID))/bookmarks/\(encoded(bookmarkID))",
            method: "DELETE"
        )
    }

    func createTimelinePin(
        projectID: String, afterMessageSeq: Int, label: String
    ) async throws {
        struct Payload: Encodable { let label: String }
        let _: EmptyResponse = try await request(
            "api/projects/\(encoded(projectID))/messages/\(afterMessageSeq)/timeline-pins",
            method: "POST", body: Payload(label: label), acceptsAnyObject: true
        )
    }

    func deleteTimelinePin(projectID: String, pinID: String) async throws {
        let _: EmptyResponse = try await request(
            "api/projects/\(encoded(projectID))/timeline-pins/\(encoded(pinID))",
            method: "DELETE"
        )
    }

    func createRole(projectID: String, name: String, onboardingPrompt: String) async throws {
        struct Payload: Encodable { let project_id: String; let name: String; let onboarding_prompt: String }
        let _: EmptyResponse = try await request(
            "api/roles", method: "POST",
            body: Payload(project_id: projectID, name: name, onboarding_prompt: onboardingPrompt),
            acceptsAnyObject: true
        )
    }

    func updateRole(id: String, name: String, onboardingPrompt: String) async throws {
        struct Payload: Encodable { let name: String; let onboarding_prompt: String }
        let _: EmptyResponse = try await request(
            "api/roles/\(id)", method: "PATCH",
            body: Payload(name: name, onboarding_prompt: onboardingPrompt),
            acceptsAnyObject: true
        )
    }

    func deleteRole(id: String) async throws {
        let _: EmptyResponse = try await request("api/roles/\(id)", method: "DELETE")
    }

    func assignRole(id: String, agentID: String, sendOnboarding: Bool) async throws {
        struct Payload: Encodable { let agent_id: String; let send_onboarding: Bool }
        let _: EmptyResponse = try await request(
            "api/roles/\(id)/assignment", method: "PUT",
            body: Payload(agent_id: agentID, send_onboarding: sendOnboarding),
            acceptsAnyObject: true
        )
    }

    func unassignRole(id: String) async throws {
        let _: EmptyResponse = try await request(
            "api/roles/\(id)/assignment", method: "DELETE"
        )
    }

    func roleHistory(id: String) async throws -> [RoleAssignment] {
        try await request("api/roles/\(id)/assignments")
    }

    func putRoleAvatar(id: String, data: Data, mediaType: String) async throws {
        var request = URLRequest(url: baseURL.appending(path: "api/roles/\(id)/avatar"))
        request.httpMethod = "PUT"
        request.httpBody = data
        request.setValue(mediaType, forHTTPHeaderField: "content-type")
        let (responseData, response) = try await URLSession.shared.data(for: request)
        try validate(response: response, data: responseData)
    }

    func deleteRoleAvatar(id: String) async throws {
        let _: EmptyResponse = try await request(
            "api/roles/\(id)/avatar", method: "DELETE"
        )
    }

    func act(on surfaceID: String, action: String) async throws {
        struct Payload: Encodable { let surface_id: String; let action: String }
        let _: EmptyResponse = try await request(
            "api/agents/action", method: "POST",
            body: Payload(surface_id: surfaceID, action: action), acceptsAnyObject: true
        )
    }

    func setNickname(localName: String, nickname: String) async throws {
        struct Payload: Encodable { let nickname: String }
        let encoded = localName.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? localName
        let _: EmptyResponse = try await request(
            "api/agents/\(encoded)/nickname", method: "PATCH",
            body: Payload(nickname: nickname), acceptsAnyObject: true
        )
    }

    func putShared(projectID: String, key: String, value: String) async throws {
        struct Payload: Encodable { let project_id: String; let key: String; let value: String }
        let _: EmptyResponse = try await request(
            "api/shared/\(key.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? key)",
            method: "PUT", body: Payload(project_id: projectID, key: key, value: value), acceptsAnyObject: true
        )
    }

    func deleteShared(projectID: String, key: String) async throws {
        let _: EmptyResponse = try await request(
            "api/shared/\(key.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? key)?project_id=\(encoded(projectID))",
            method: "DELETE"
        )
    }

    func createProject(name: String) async throws -> FungisProject {
        struct Payload: Encodable { let name: String }
        return try await request("api/projects", method: "POST", body: Payload(name: name))
    }

    func updateProject(id: String, name: String) async throws -> FungisProject {
        struct Payload: Encodable { let name: String }
        return try await request(
            "api/projects/\(encoded(id))", method: "PATCH", body: Payload(name: name)
        )
    }

    func archiveProject(id: String) async throws {
        let _: EmptyResponse = try await request(
            "api/projects/\(encoded(id))", method: "DELETE", acceptsAnyObject: true
        )
    }

    func setProjectRepository(projectID: String, path: String) async throws {
        struct Payload: Encodable { let path: String }
        let _: EmptyResponse = try await request(
            "api/projects/\(encoded(projectID))/repository", method: "PUT",
            body: Payload(path: path), acceptsAnyObject: true
        )
    }

    func deleteProjectRepository(projectID: String) async throws {
        let _: EmptyResponse = try await request(
            "api/projects/\(encoded(projectID))/repository", method: "DELETE"
        )
    }

    func updatePMProfile(displayName: String) async throws {
        struct Payload: Encodable { let display_name: String }
        let _: EmptyResponse = try await request(
            "api/pm-profile", method: "PATCH", body: Payload(display_name: displayName),
            acceptsAnyObject: true
        )
    }

    func putPMAvatar(data: Data, mediaType: String) async throws {
        var request = URLRequest(url: baseURL.appending(path: "api/pm-profile/avatar"))
        request.httpMethod = "PUT"
        request.httpBody = data
        request.setValue(mediaType, forHTTPHeaderField: "content-type")
        let (responseData, response) = try await URLSession.shared.data(for: request)
        try validate(response: response, data: responseData)
    }

    func deletePMAvatar() async throws {
        let _: EmptyResponse = try await request("api/pm-profile/avatar", method: "DELETE")
    }

    private func encoded(_ value: String) -> String {
        value.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? value
    }

    private func request<Response: Decodable>(
        _ path: String,
        method: String = "GET",
        body: (any Encodable)? = nil,
        acceptsAnyObject: Bool = false
    ) async throws -> Response {
        var request = URLRequest(url: endpoint(path))
        request.httpMethod = method
        if let body {
            request.httpBody = try JSONEncoder().encode(AnyEncodable(body))
            request.setValue("application/json", forHTTPHeaderField: "content-type")
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw FungisAPIError.invalidResponse
        }
        guard 200..<300 ~= http.statusCode else {
            let detail = (try? JSONDecoder().decode(ErrorBody.self, from: data).detail)
                ?? String(data: data, encoding: .utf8) ?? "unknown error"
            throw FungisAPIError.server(http.statusCode, detail)
        }
        if Response.self == EmptyResponse.self {
            if acceptsAnyObject || data.isEmpty { return EmptyResponse() as! Response }
        }
        return try JSONDecoder().decode(Response.self, from: data)
    }

    private func validate(response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else {
            throw FungisAPIError.invalidResponse
        }
        guard 200..<300 ~= http.statusCode else {
            let detail = (try? JSONDecoder().decode(ErrorBody.self, from: data).detail)
                ?? String(data: data, encoding: .utf8) ?? "unknown error"
            throw FungisAPIError.server(http.statusCode, detail)
        }
    }

    private func endpoint(_ value: String) -> URL {
        let parts = value.split(separator: "?", maxSplits: 1, omittingEmptySubsequences: false)
        var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)!
        components.path = "/" + String(parts[0]).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        if parts.count == 2 { components.percentEncodedQuery = String(parts[1]) }
        return components.url!
    }
}

private struct ErrorBody: Decodable { let detail: String }
private struct EmptyResponse: Codable {}

private struct AnyEncodable: Encodable {
    private let encodeValue: (Encoder) throws -> Void
    init(_ value: any Encodable) { encodeValue = value.encode }
    func encode(to encoder: Encoder) throws { try encodeValue(encoder) }
}
