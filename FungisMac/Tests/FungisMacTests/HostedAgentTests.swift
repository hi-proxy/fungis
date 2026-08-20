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
