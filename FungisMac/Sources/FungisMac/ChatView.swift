import SwiftUI

enum InspectorTab: String, CaseIterable, Identifiable {
    case pins = "Pins"
    case roles = "Roles"
    case shared = "Shared"
    case work = "Work"
    var id: Self { self }
}

struct ChatView: View {
    @EnvironmentObject private var model: AppModel
    @State private var showInspector = false
    @State private var inspectorTab: InspectorTab = .pins
    @State private var scrollProxy: ScrollViewProxy?
    @State private var answering: AttentionRequest?
    @State private var showingBoard = false
    @State private var bookmarking: ChatMessage?
    @State private var pinningAfter: ChatMessage?
    @State private var contextFilter: String?
    @State private var activeTimelinePinID: String?
    @State private var flashingTimelinePinID: String?

    var body: some View {
        VStack(spacing: 0) {

            // 상황판은 타임라인 맨 위에 붙는다. 어느 방에 있든 전체가 보인다.
            // 비어 있어도 띄운다. 안 띄우면 처음 붙일 자리가 없다.
            BoardStrip(
                tracks: model.board.tracks,
                currentProjectID: model.selectedProjectID
            ) { showingBoard = true }

            if !model.snapshot.attention.isEmpty {
                ScrollView(.horizontal) {
                    HStack(spacing: 10) {
                        ForEach(model.snapshot.attention) { request in
                            AttentionCard(request: request) {
                                answering = request
                            }
                        }
                    }.padding(.horizontal, 20).padding(.top, 12).padding(.bottom, 10)
                }.scrollIndicators(.hidden)
            }

            // 상단 태그칩 트레이는 미노출. 안 쓰는 기능이 타임라인 위 한 줄을
            // 계속 차지했다. 메시지에 붙는 태그 표시는 그대로 둔다 — 거기서는
            // 무슨 얘기인지 알려주는 값을 한다.
            // ContextTagTray는 지우지 않는다. 필터 자체가 없어진 것은 아니고
            // 어디에 둘지가 아직 안 정해졌다.

            ScrollViewReader { proxy in
                GeometryReader { timelineGeometry in
                    HStack(spacing: 0) {
                        ScrollView {
                            LazyVStack(spacing: 8) {
                                Color.clear.frame(height: 1).id("chat-bottom")
                                if filteredTimeline.isEmpty, !model.isLoadingTimeline {
                                    Group { isHQ ? AnyView(emptyHQGuide) : AnyView(emptyRoomGuide) }
                                        // 뒤집힌 스택 안이라 한쪽 패딩은 방향이
                                        // 함께 반전된다. 대칭으로 둬서 무관하게 만든다.
                                        .padding(.vertical, 60)
                                        .scaleEffect(x: 1, y: -1, anchor: .center)
                                }
                                ForEach(filteredTimeline.reversed()) { message in
                                    VStack(spacing: 0) {
                                        MessageRow(
                                            message: message, pmID: model.snapshot.pmID,
                                            pmProfile: model.snapshot.pmProfile,
                                            roles: model.snapshot.roles,
                                            leadRooms: leadRooms,
                                            isBookmarked: bookmarkedSequences.contains(message.seq)
                                        ) {
                                            contextFilter = contextFilter == $0 ? nil : $0
                                        } bookmark: {
                                            bookmarking = message
                                        }
                                        .id(message.id)
                                        .onAppear {
                                            if message.seq == historyPreloadSequence {
                                                Task { await model.loadOlderMessages() }
                                            }
                                        }
                                        if contextFilter == nil {
                                            if let pin = timelinePin(after: message.seq) {
                                                TimelinePinDivider(
                                                    pin: pin,
                                                    flashing: flashingTimelinePinID == pin.id
                                                ) {
                                                    Task { await model.deleteTimelinePin(id: pin.id) }
                                                }
                                                .background {
                                                    GeometryReader { geometry in
                                                        Color.clear.preference(
                                                            key: TimelinePinPositionPreferenceKey.self,
                                                            value: [
                                                                pin.id: geometry.frame(
                                                                    in: .named("chat-timeline")
                                                                ).midY
                                                            ]
                                                        )
                                                    }
                                                }
                                            } else {
                                                TimelinePinGap { pinningAfter = message }
                                            }
                                        }
                                    }
                                    .scaleEffect(x: 1, y: -1, anchor: .center)
                                }
                            }
                            .padding(.horizontal, 20).padding(.vertical, 16)
                        }
                        .scrollIndicators(.hidden)
                        .scaleEffect(x: 1, y: -1, anchor: .center)
                        .coordinateSpace(name: "chat-timeline")
                        .overlay(alignment: .bottomTrailing) {
                            Button {
                                scrollToBottom(proxy, animated: false)
                            } label: {
                                Image(systemName: "arrow.down.to.line.compact")
                                    .font(.body.bold())
                                    .frame(width: 30, height: 30)
                            }
                            .buttonStyle(.borderedProminent)
                            .buttonBorderShape(.circle)
                            .help("맨 아래로 이동")
                            .padding(16)
                        }
                        // 타임라인을 역순으로 쌓고 ScrollView 자신을 뒤집는다.
                        // 오프셋 0이 곧 최신이므로 과거를 앞에 붙여도 이미
                        // 배치된 행을 다시 재지 않는다.
                        //
                        // scaleEffect는 렌더링 변환이라 레이아웃과 스크롤
                        // 오프셋의 의미를 바꾸지 않는다. 그래서 content에 걸면
                        // 안 되고 ScrollView 자신에 걸어야 한다. content에
                        // 걸었더니 과거가 붙을 때마다 콘텐츠 높이가 늘어
                        // 최신이 화면 밖으로 밀렸다.
                        //
                        // coordinateSpace와 overlay는 반드시 scaleEffect 뒤에
                        // 온다. 앞에 두면 핀 좌표의 반전이 한 번만 누적되어
                        // rail 점등 판정이 틀어지고, 버튼이 거꾸로 그려진다.
                    }
                    .background(Color(nsColor: .textBackgroundColor).opacity(0.45))
                    .onAppear {
                        // 핀·북마크 점프가 쓴다. 최신 위치는 오프셋 0이 곧
                        // 최신이라 여기서 스크롤을 지시할 필요가 없다.
                        scrollProxy = proxy
                    }
                    .onChange(of: model.selectedProjectID) {
                        activeTimelinePinID = nil
                        flashingTimelinePinID = nil
                        // ChatView는 방을 바꿔도 재생성되지 않아 이전 방에서
                        // 올려둔 오프셋이 남는다. 최신 위치로 되돌린다.
                        if !model.snapshot.timeline.isEmpty {
                            scrollToBottom(proxy, animated: false, settleLayout: true)
                        }
                    }
                    .onPreferenceChange(TimelinePinPositionPreferenceKey.self) {
                        updateActiveTimelinePin(
                            pinPositions: $0,
                            viewportHeight: timelineGeometry.size.height
                        )
                    }
                }
            }
            ChatComposer(
                tracks: tracks, gitBranches: gitBranches,
                recipientLabel: recipientLabel
            )
            .id(model.selectedProjectID)
        }
        .toolbar {
            ToolbarItem {
                Button {
                    showInspector.toggle()
                } label: {
                    Image(systemName: "sidebar.right")
                }.help("Pins, roles, shared and work")
            }
        }
        .onChange(of: model.showsRoleSetup) {
            guard model.showsRoleSetup else { return }
            inspectorTab = .roles
            showInspector = true
            model.showsRoleSetup = false
        }
        .inspector(isPresented: $showInspector) {
            inspectorPanel.inspectorColumnWidth(min: 260, ideal: 320, max: 480)
        }
        .sheet(isPresented: $showingBoard) {
            BoardSheet().environmentObject(model)
        }
        .sheet(item: $answering) { request in
            AttentionAnswerSheet(request: request)
        }
        .sheet(item: $bookmarking) { message in
            BookmarkEditor(message: message)
        }
        .sheet(item: $pinningAfter) { message in
            TimelinePinEditor(after: message)
        }
    }

    /// 방을 새로 만들면 무엇부터 해야 하는지가 화면에 없다. 남은 단계만
    /// 보여주고 끝나면 사라진다. 온보딩은 방마다 한 번이라 상시 UI를 바꾸는
    /// 대신 빈 상태에만 둔다.
    /// HQ 발신자는 역할이 없어 아바타 폴백이 전원 회색 "CL"이 된다. lead 는
    /// 자기 방을 대표해 말하는 것이므로 그 방의 아바타를 입힌다.
    private var leadRooms: [String: String] {
        Dictionary(
            uniqueKeysWithValues: (model.board.candidates ?? []).compactMap { candidate in
                candidate.lead?.agentID.map { ($0, candidate.name) }
            }
        )
    }

    private var isHQ: Bool {
        model.snapshot.projects.first { $0.id == model.selectedProjectID }?.isHQ == true
    }

    /// HQ에는 역할이 없다. 프로젝트용 3단계를 그대로 띄우면 영원히 준비가 안 된
    /// 방으로 보인다. 여기서 할 일은 방을 부르는 것이지 역할을 만드는 것이 아니다.
    private var emptyHQGuide: some View {
        VStack(spacing: 14) {
            Image(systemName: "point.3.connected.trianglepath.dotted")
                .font(.system(size: 34)).foregroundStyle(.tertiary)
            Text("여기서 여러 방을 함께 본다").font(.title3.bold())
            Text("소집한 방의 lead만 이 타임라인을 읽는다. 각 방의 진행은 위 상황판에 쌓인다.")
                .font(.callout).foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button("소집 열기", systemImage: "person.2.badge.plus") {
                inspectorTab = .roles
                showInspector = true
            }
            .buttonStyle(.borderedProminent)
        }
        .frame(maxWidth: 380)
        .frame(maxWidth: .infinity)
    }

    private var emptyRoomGuide: some View {
        VStack(spacing: 14) {
            Image(systemName: "bubble.left.and.bubble.right")
                .font(.system(size: 34)).foregroundStyle(.tertiary)
            Text(model.snapshot.roles.isEmpty ? "이 방을 쓸 준비를 하자" : "대화를 시작하자")
                .font(.title3.bold())
            VStack(alignment: .leading, spacing: 10) {
                setupStep(
                    number: 1, title: "역할 만들기",
                    detail: "front1처럼 계속 유지할 담당자 주소를 만든다.",
                    done: !model.snapshot.roles.isEmpty
                )
                setupStep(
                    number: 2, title: "에이전트 배정",
                    detail: "역할 카드의 Assign에서 연결까지 함께 끝낸다.",
                    done: model.snapshot.roles.contains(where: \.assigned)
                )
                setupStep(
                    number: 3, title: "Initialize",
                    detail: "배정된 역할에게 Fungis 사용법을 보낸다.",
                    done: false
                )
            }
            .padding(16)
            .background(.quaternary.opacity(0.25), in: RoundedRectangle(cornerRadius: 12))
            Button("Roles 열기", systemImage: "person.badge.key") {
                inspectorTab = .roles
                showInspector = true
            }
            .buttonStyle(.borderedProminent)
        }
        .frame(maxWidth: 380)
        .frame(maxWidth: .infinity)
    }

    private func setupStep(
        number: Int, title: String, detail: String, done: Bool
    ) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: done ? "checkmark.circle.fill" : "\(number).circle")
                .foregroundStyle(done ? Color.green : Color.secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.callout.weight(.medium))
                    .strikethrough(done, color: .secondary)
                    .foregroundStyle(done ? .secondary : .primary)
                Text(detail).font(.caption).foregroundStyle(.tertiary)
            }
            Spacer(minLength: 0)
        }
    }

    /// 에이전트가 터미널에서 멈춰 기다리는 중이다. 무엇을 하려는지 그대로
    private var inspectorPanel: some View {
        VStack(spacing: 0) {
            Picker("", selection: $inspectorTab) {
                ForEach(InspectorTab.allCases) { tab in
                    Text(tab.rawValue).tag(tab)
                }
            }
            .pickerStyle(.segmented).labelsHidden()
            .padding(.horizontal, 12).padding(.vertical, 10)
            Divider()
            switch inspectorTab {
            case .pins: timelineRail
            case .roles: RolesView()
            case .shared: SharedView()
            case .work: WorkView()
            }
        }
    }

    private var timelineRail: some View {
        TimelineSideRail(
            pins: model.snapshot.timelinePins,
            bookmarks: model.snapshot.bookmarks,
            activePinID: activeTimelinePinID,
            jumpPin: { pin in
                contextFilter = nil
                Task {
                    await model.ensureMessageLoaded(pin.afterMessageSeq)
                    await Task.yield()
                    scrollProxy?.scrollTo(pin.afterMessageSeq, anchor: .center)
                    await flashTimelinePin(pin.id)
                }
            },
            jumpBookmark: { bookmark in
                contextFilter = nil
                Task {
                    await model.ensureMessageLoaded(bookmark.messageSeq)
                    await Task.yield()
                    withAnimation(.easeInOut(duration: 0.2)) {
                        scrollProxy?.scrollTo(bookmark.messageSeq, anchor: .center)
                    }
                }
            },
            deletePin: { pin in
                Task { await model.deleteTimelinePin(id: pin.id) }
            },
            deleteBookmark: { bookmark in
                Task { await model.deleteBookmark(id: bookmark.id) }
            }
        )
    }

    private func scrollToBottom(
        _ proxy: ScrollViewProxy, animated: Bool, settleLayout: Bool = false
    ) {
        Task { @MainActor in
            await Task.yield()
            if settleLayout {
                try? await Task.sleep(for: .milliseconds(60))
            }
            if animated {
                withAnimation(.easeOut(duration: 0.2)) {
                    proxy.scrollTo("chat-bottom", anchor: .top)
                }
            } else {
                proxy.scrollTo("chat-bottom", anchor: .top)
            }
        }
    }

    private var tracks: [String] {
        Array(Set(model.snapshot.timeline.compactMap(\.track))).sorted()
    }

    private var gitBranches: [String] {
        if let repository = model.snapshot.projectRepositories.first(where: {
            $0.projectID == model.selectedProjectID
        }), let git = repository.git {
            return git.branches.sorted()
        }
        return Array(Set(model.snapshot.agents.compactMap { agent in
            guard agent.connected, agent.git?.verified == true else { return nil }
            return agent.git?.branch
        })).sorted()
    }

    private var contexts: [String] {
        Array(Set(model.snapshot.timeline.flatMap { message in
            (message.track.map { [$0] } ?? []) + message.tags
                + message.detectedContexts.filter(\.verified).map(\.value)
        })).sorted()
    }

    private var filteredTimeline: [ChatMessage] {
        guard let contextFilter else { return model.snapshot.timeline }
        return model.snapshot.timeline.filter {
            $0.track == contextFilter || $0.tags.contains(contextFilter)
                || $0.detectedContexts.contains {
                    $0.verified && $0.value == contextFilter
                }
        }
    }

    private var historyPreloadSequence: Int? {
        guard model.hasOlderMessages, !filteredTimeline.isEmpty else { return nil }
        return filteredTimeline[min(5, filteredTimeline.count - 1)].seq
    }

    private var bookmarkedSequences: Set<Int> {
        Set(model.snapshot.bookmarks.map(\.messageSeq))
    }

    private func timelinePin(after messageSequence: Int) -> TimelinePin? {
        model.snapshot.timelinePins.first { $0.afterMessageSeq == messageSequence }
    }

    private func updateActiveTimelinePin(
        pinPositions: [String: CGFloat], viewportHeight: CGFloat
    ) {
        guard contextFilter == nil, !pinPositions.isEmpty else {
            activeTimelinePinID = nil
            return
        }
        activeTimelinePinID = TimelinePinTracker.activePinID(
            positions: pinPositions, viewportHeight: viewportHeight
        )
    }

    @MainActor
    private func flashTimelinePin(_ id: String) async {
        try? await Task.sleep(for: .milliseconds(220))
        withAnimation(.easeIn(duration: 0.08)) {
            flashingTimelinePinID = id
        }
        try? await Task.sleep(for: .milliseconds(260))
        guard flashingTimelinePinID == id else { return }
        withAnimation(.easeOut(duration: 0.3)) {
            flashingTimelinePinID = nil
        }
    }

    private var recipientLabel: String {
        let roleNames = model.snapshot.roles.filter {
            model.selectedRoles.contains($0.id)
        }.map(\.name)
        let selected = model.snapshot.targets.filter {
            model.selectedTargets.contains($0.id)
        }
        let names = roleNames + selected.map(\.displayName)
        let copied = model.snapshot.roles.filter {
            model.referenceRoles.contains($0.id)
        }.map(\.name)
        let suffix = copied.isEmpty ? "" : "  ·  cc: " + copied.joined(separator: ", ")
        if names.isEmpty { return "Select recipients" + suffix }
        if names.count == 1 { return names[0] + suffix }
        return "\(names.count) recipients" + suffix
    }
}

private struct ChatComposer: View {
    /// HQ에는 고를 역할이 없다. 거기 글은 소집된 lead 전원이 받으므로
    /// 지정하지 않는 것이 곧 전원이다.
    private var isHQ: Bool {
        model.snapshot.projects.first { $0.id == model.selectedProjectID }?.isHQ == true
    }

    @EnvironmentObject private var model: AppModel
    let tracks: [String]
    let gitBranches: [String]
    let recipientLabel: String
    @State private var draft = ""
    @State private var draftTrack = ""
    @State private var draftTags = ""
    @State private var showMetadata = false
    @State private var mentionError: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            if showMetadata {
                HStack(spacing: 8) {
                    Menu {
                        Button("No track") { draftTrack = "" }
                        ForEach(tracks, id: \.self) { track in
                            Button(track) { draftTrack = track }
                        }
                        if !gitBranches.isEmpty {
                            Divider()
                            Section("Verified Git branches") {
                                ForEach(gitBranches, id: \.self) { branch in
                                    Button(branch) { draftTrack = branch }
                                }
                            }
                        }
                    } label: {
                        Label("Track", systemImage: "point.topleft.down.to.point.bottomright.curvepath")
                    }
                    TextField("branch/feature or topic", text: $draftTrack)
                        .textFieldStyle(.roundedBorder).frame(maxWidth: 260)
                    TextField("tags: ticket/ARC-42, review", text: $draftTags)
                        .textFieldStyle(.roundedBorder)
                }
            }
            participantSelector
            HStack(alignment: .bottom, spacing: 9) {
                Button {
                    withAnimation(.easeInOut(duration: 0.16)) { showMetadata.toggle() }
                } label: {
                    Image(systemName: showMetadata ? "xmark" : "plus")
                        .frame(width: 28, height: 28)
                }
                .buttonStyle(.borderless).help("Track and tags")
                TextEditor(text: $draft)
                    .font(.body).autocorrectionDisabled()
                    .frame(minHeight: 44, maxHeight: 120)
                    .scrollContentBackground(.hidden)
                    .padding(.horizontal, 8).padding(.vertical, 5)
                    .onKeyPress(keys: [.return], phases: .down) { press in
                        guard press.modifiers.isEmpty, startsWithMention else {
                            return .ignored
                        }
                        Task { await send() }
                        return .handled
                    }
                    .onChange(of: draft) { mentionError = nil }
                    .disabled(blockedBy != nil)
                Button { Task { await send() } } label: {
                    Image(systemName: "arrow.up").font(.body.bold())
                        .frame(width: 30, height: 30)
                }
                .buttonStyle(.borderedProminent).buttonBorderShape(.circle)
                .disabled(
                    blockedBy != nil
                        || draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                        || (!isHQ
                            && !startsWithMention
                            && model.selectedTargets.isEmpty
                            && model.selectedRoles.isEmpty)
                )
            }
            .background(.quaternary.opacity(0.28), in: RoundedRectangle(cornerRadius: 14))
            .opacity(blockedBy == nil ? 1 : 0.25)
            // 입력창 위에 덮는다. 위쪽에 따로 자리를 내주면 화면만 먹고, 정작
            // 막혀 있는 곳과 떨어져 있어 무엇 때문에 못 보내는지 눈에 안 들어온다.
            .overlay {
                if let blocked = blockedBy { permissionOverlay(blocked) }
            }
            HStack {
                if blockedBy != nil {
                    // 띠가 이미 무엇 때문인지 말한다. 여기서는 결과만 말한다.
                    Text("지금 보내도 읽지 못한다")
                        .font(.caption).foregroundStyle(.orange)
                } else if let mentionError {
                    Label(mentionError, systemImage: "exclamationmark.triangle.fill")
                        .font(.caption).foregroundStyle(.red)
                } else {
                    Text("To: \(recipientLabel)").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if !draftTrack.isEmpty {
                    Label(
                        draftTrack,
                        systemImage: "point.topleft.down.to.point.bottomright.curvepath"
                    ).font(.caption2)
                }
            }
        }
        .padding(.horizontal, 16).padding(.vertical, 12).background(.bar)
    }

    /// 터미널이 권한 확인에서 멈춰 있으면 보내도 읽지 못한다. 그 사실을
    /// 화면이 감추면 PM은 보냈다고 믿고 기다린다.
    ///
    /// 지금 보내려는 상대가 멈춰 있을 때만 막는다. 방에 여럿이 있는데 하나
    /// 멈췄다고 전부 막으면 멀쩡한 상대에게도 말을 못 건다.
    ///
    /// 쌓지 않고 최신 하나만 본다. 터미널은 한 번에 하나만 막힌다.
    private var blockedBy: PermissionRequest? {
        let recipients = Set(
            model.snapshot.roles
                .filter { model.selectedRoles.contains($0.id) }
                .compactMap(\.agentID)
        ).union(
            model.snapshot.targets
                .filter { model.selectedTargets.contains($0.id) }
                .map(\.principalID)
        )
        return model.snapshot.permissionRequests.last {
            guard let agent = $0.agentID else { return false }
            return recipients.contains(agent)
        }
    }

    /// 입력창을 덮는 띠. 승인·거절 버튼을 두지 않는다 — 이 provider에서는
    /// 명령형 hook이 결정을 돌려줄 수 없어서 눌러도 터미널은 계속 멈춰 있다.
    /// 되는 것처럼 보이는 버튼이 제일 나쁘다.
    private func permissionOverlay(_ request: PermissionRequest) -> some View {
        HStack(spacing: 9) {
            Image(systemName: "lock.trianglebadge.exclamationmark.fill")
                .foregroundStyle(.orange)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(request.agentName ?? "에이전트").font(.caption.bold())
                    Text("터미널에서 답해야 풀린다")
                        .font(.caption2).foregroundStyle(.secondary)
                    Text(request.toolName)
                        .font(.system(size: 9, weight: .bold))
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .background(.quaternary.opacity(0.6), in: Capsule())
                }
                Text(request.summary)
                    .font(.caption2.monospaced()).foregroundStyle(.secondary)
                    .lineLimit(1).truncationMode(.middle)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.orange.opacity(0.14), in: RoundedRectangle(cornerRadius: 14))
        .overlay(
            RoundedRectangle(cornerRadius: 14).stroke(Color.orange.opacity(0.45))
        )
    }

    private var participantSelector: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 6) {
                HStack(spacing: 5) {
                    PMAvatar(profile: model.snapshot.pmProfile, size: 22)
                    Text(model.snapshot.pmProfile.displayName).font(.caption.bold())
                }
                .padding(.horizontal, 8).padding(.vertical, 6)
                .background(Color.purple.opacity(0.09), in: Capsule())
                ForEach(model.snapshot.roles) { role in
                    let isReference = model.referenceRoles.contains(role.id)
                    Button { cycleRole(role) } label: {
                        HStack(spacing: 5) {
                            RoleAvatar(role: role, size: 22)
                            Text(role.name).font(.caption.bold()).lineLimit(1)
                            Circle()
                                .fill(role.assigned && role.sessionConnected ? .green : .orange)
                                .frame(width: 7, height: 7)
                            if isReference {
                                Text("CC")
                                    .font(.system(size: 9, weight: .heavy))
                                    .foregroundStyle(.white)
                                    .padding(.horizontal, 4).padding(.vertical, 1)
                                    .background(Color.blue, in: Capsule())
                            }
                        }
                        .padding(.horizontal, 8).padding(.vertical, 6)
                        // 캡슐 전체가 눌려야 한다. 안 주면 그려진 글자와
                        // 아바타만 눌리고 여백은 통과한다.
                        .contentShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .background(chipTint(role), in: Capsule())
                    // 세 자리가 눈에 바로 갈리게 한다. 채워짐=수신,
                    // 테두리만=참조, 아무것도 없음=해제. 옅은 색 차이만으로는
                    // 참조와 해제가 구분되지 않았다.
                    .overlay(
                        Capsule().stroke(
                            isReference ? Color.blue : .clear, lineWidth: 1.5
                        )
                    )
                    .help(
                        isReference
                            ? "참조 — 읽지만 답하지 않는다"
                            : "누를 때마다 수신 → 참조 → 해제"
                    )
                }
                directSessionMenu
            }
        }
        .scrollIndicators(.hidden)
        .frame(maxWidth: .infinity, alignment: .leading)
        .fixedSize(horizontal: false, vertical: true)
    }

    /// 역할 칩으로 표현할 수 없는 직접 세션 수신자를 고른다.
    private var directSessionMenu: some View {
        Menu {
            if model.snapshot.targets.isEmpty {
                Text("No connected sessions")
            }
            ForEach(model.snapshot.targets) { target in
                Button {
                    if model.selectedTargets.contains(target.id) {
                        model.selectedTargets.remove(target.id)
                    } else {
                        model.selectedTargets.insert(target.id)
                    }
                } label: {
                    Label(
                        target.displayName,
                        systemImage: model.selectedTargets.contains(target.id)
                            ? "checkmark.circle.fill" : "circle"
                    )
                }
            }
        } label: {
            HStack(spacing: 5) {
                Image(systemName: "terminal")
                if directCount > 0 {
                    Text("\(directCount)").font(.caption.bold())
                }
            }.padding(.horizontal, 8).padding(.vertical, 6)
        }
        .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
        .background(
            directCount > 0 ? Color.accentColor.opacity(0.16) : Color.secondary.opacity(0.07),
            in: Capsule()
        )
        .help("Direct session recipients")
    }

    private var directCount: Int {
        model.snapshot.targets.filter { model.selectedTargets.contains($0.id) }.count
    }

    private func chipTint(_ role: WorkspaceRole) -> Color {
        if model.selectedRoles.contains(role.id) { return .accentColor.opacity(0.22) }
        // 참조는 채우지 않는다. 채우면 수신과 헷갈리고, 옅게 채우면 해제와
        // 헷갈린다. 테두리로만 말한다.
        if model.referenceRoles.contains(role.id) { return .clear }
        return .secondary.opacity(0.07)
    }

    /// 수신 → 참조 → 해제. 배정되지 않은 역할은 참조를 보낼 principal이 없어
    /// 두 단계만 돈다.
    private func cycleRole(_ role: WorkspaceRole) {
        if model.selectedRoles.contains(role.id) {
            model.selectedRoles.remove(role.id)
            if role.agentID != nil { model.referenceRoles.insert(role.id) }
        } else if model.referenceRoles.contains(role.id) {
            model.referenceRoles.remove(role.id)
        } else {
            model.selectedRoles.insert(role.id)
        }
    }

    private func send() async {
        let rawBody: String
        let targets: [String]
        let roles: [String]
        switch MentionRouting.parse(draft, candidates: mentionCandidates) {
        case .success(let route):
            rawBody = route.body
            targets = route.targetIDs
            roles = route.roleIDs
            model.selectedTargets = Set(targets)
            model.selectedRoles = Set(roles)
        case .failure(let error):
            mentionError = error.message
            return
        case nil:
            rawBody = draft
            targets = model.snapshot.targets
                .filter { model.selectedTargets.contains($0.id) }
                .map(\.id)
            roles = Array(model.selectedRoles)
        }
        // HQ 는 비워서 보내는 것이 곧 전원이다. 버튼만 열고 여기서 조용히
        // 돌아가면 눌리는데 아무 일도 안 일어나는 버튼이 된다 — 보드 잇기에서
        // 세 번 밟은 그 무늬다.
        guard isHQ || !targets.isEmpty || !roles.isEmpty else { return }
        let body = rawBody.trimmingCharacters(in: .whitespacesAndNewlines)
        let track = draftTrack.trimmingCharacters(in: .whitespacesAndNewlines)
        let tags = draftTags.split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        if await model.send(
            body, to: targets, roles: roles,
            references: model.selectedReferenceIDs,
            track: track.isEmpty ? nil : track, tags: tags
        ) {
            draft = ""
        }
    }

    private var startsWithMention: Bool {
        draft.trimmingCharacters(in: .whitespacesAndNewlines).hasPrefix("@")
    }

    private var mentionCandidates: [MentionCandidate] {
        let roles = model.snapshot.roles.map {
            MentionCandidate(token: $0.name, id: $0.id, kind: .role)
        }
        let targets = model.snapshot.targets.flatMap { target in
            var aliases = [target.displayName]
            if target.localName.caseInsensitiveCompare(target.displayName) != .orderedSame {
                aliases.append(target.localName)
            }
            return aliases.map {
                MentionCandidate(token: $0, id: target.id, kind: .target)
            }
        }
        return roles + targets
    }
}

private struct AttentionAnswerSheet: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let request: AttentionRequest
    @State private var answer = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Answer \(request.senderName)").font(.title2.bold())
            Text(MessagePrettyPrinter.prettyText(request.body, seed: request.seq))
                .foregroundStyle(.secondary)
            TextEditor(text: $answer)
                .autocorrectionDisabled()
                .frame(height: 130).padding(6)
                .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Send") {
                    Task {
                        if await model.send(
                            answer, to: [request.senderID], inReplyTo: request.seq
                        ) {
                            dismiss()
                        }
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(24).frame(width: 480)
    }
}

private struct BookmarkEditor: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let message: ChatMessage
    @State private var label = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label("메시지 북마크", systemImage: "bookmark.fill")
                .font(.title2.bold()).foregroundStyle(.orange)
            Text(message.body)
                .lineLimit(4).foregroundStyle(.secondary)
                .padding(12).frame(maxWidth: .infinity, alignment: .leading)
                .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 10))
            TextField("예: 디자인 웨이브1 완료", text: $label)
                .textFieldStyle(.roundedBorder)
            HStack {
                Text("#\(message.displaySeq)").font(.caption).foregroundStyle(.tertiary)
                Spacer()
                Button("취소") { dismiss() }
                Button("추가") {
                    Task {
                        if await model.createBookmark(
                            messageSeq: message.seq,
                            label: label.trimmingCharacters(in: .whitespacesAndNewlines)
                        ) {
                            dismiss()
                        }
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(24).frame(width: 460)
    }
}

private struct TimelinePinEditor: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let after: ChatMessage
    @State private var label = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label("타임라인 구간 핀", systemImage: "mappin.and.ellipse")
                .font(.title2.bold()).foregroundStyle(.blue)
            Text("이 메시지 다음에 구간 경계를 만듭니다.")
                .font(.callout).foregroundStyle(.secondary)
            Text(after.body)
                .lineLimit(4).foregroundStyle(.secondary)
                .padding(12).frame(maxWidth: .infinity, alignment: .leading)
                .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 10))
            TextField("예: 디자인 웨이브 2 완료", text: $label)
                .textFieldStyle(.roundedBorder)
                .onSubmit { createPin() }
            HStack {
                Text("#\(after.displaySeq) 다음").font(.caption).foregroundStyle(.tertiary)
                Spacer()
                Button("취소") { dismiss() }
                Button("핀 추가") { createPin() }
                    .buttonStyle(.borderedProminent)
                    .disabled(trimmedLabel.isEmpty)
            }
        }
        .padding(24).frame(width: 460)
    }

    private var trimmedLabel: String {
        label.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func createPin() {
        guard !trimmedLabel.isEmpty else { return }
        Task {
            if await model.createTimelinePin(
                afterMessageSeq: after.seq, label: trimmedLabel
            ) {
                dismiss()
            }
        }
    }
}

private struct TimelinePinGap: View {
    let add: () -> Void
    @State private var hovered = false

    var body: some View {
        ZStack {
            Rectangle().fill(Color.clear)
            Rectangle()
                .fill(Color.secondary.opacity(hovered ? 0.22 : 0))
                .frame(height: 1)
            if hovered {
                Button(action: add) {
                    Label("Pin", systemImage: "plus")
                        .font(.caption2.bold())
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(.regularMaterial, in: Capsule())
                        .contentShape(Capsule())
                }
                .buttonStyle(.plain)
            }
        }
        .frame(height: 18)
        .contentShape(Rectangle())
        .onHover { hovered = $0 }
    }
}

private struct TimelinePinDivider: View {
    let pin: TimelinePin
    let flashing: Bool
    let delete: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Rectangle().fill(pinColor.opacity(0.35)).frame(height: 1)
            Label(pin.label, systemImage: "mappin")
                .font(.caption.bold()).foregroundStyle(pinColor)
                .lineLimit(1)
                .padding(.horizontal, 9).padding(.vertical, 4)
                .background(pinColor.opacity(0.1), in: Capsule())
            Rectangle().fill(pinColor.opacity(0.35)).frame(height: 1)
        }
        .padding(.horizontal, 8).padding(.vertical, 6)
        .background(
            flashing ? Color.yellow.opacity(0.34) : .clear,
            in: RoundedRectangle(cornerRadius: 8)
        )
        .contextMenu {
            Button("구간 핀 삭제", role: .destructive, action: delete)
        }
    }

    private var pinColor: Color { contextColor(pin.label) }
}

private struct TimelineSideRail: View {
    let pins: [TimelinePin]
    let bookmarks: [MessageBookmark]
    let activePinID: String?
    let jumpPin: (TimelinePin) -> Void
    let jumpBookmark: (MessageBookmark) -> Void
    let deletePin: (TimelinePin) -> Void
    let deleteBookmark: (MessageBookmark) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("Timeline Pins", systemImage: "point.3.connected.trianglepath.dotted")
                .font(.subheadline.bold())
                .padding(.horizontal, 14).padding(.top, 14)
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    if pins.isEmpty {
                        Text("메시지 사이에 마우스를 올려\n+ Pin으로 구간을 표시하세요.")
                            .font(.caption).foregroundStyle(.secondary)
                            .padding(.horizontal, 14)
                    } else {
                        VStack(alignment: .leading, spacing: 0) {
                            ForEach(pins) { pin in
                                pinRow(pin)
                            }
                        }
                    }

                    Divider().padding(.horizontal, 14)

                    VStack(alignment: .leading, spacing: 10) {
                        Label("Message Bookmarks", systemImage: "bookmark")
                            .font(.subheadline.bold())
                        if bookmarks.isEmpty {
                            Text("저장한 메시지가 없습니다.")
                                .font(.caption).foregroundStyle(.secondary)
                        } else {
                            ForEach(bookmarks) { bookmark in
                                bookmarkRow(bookmark)
                            }
                        }
                    }
                    .padding(.horizontal, 14)
                }
                .padding(.bottom, 14)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private func pinRow(_ pin: TimelinePin) -> some View {
        let active = pin.id == activePinID
        let color = contextColor(pin.label)
        return Button { jumpPin(pin) } label: {
            HStack(alignment: .top, spacing: 9) {
                ZStack(alignment: .top) {
                    Rectangle().fill(Color.secondary.opacity(0.18)).frame(width: 2)
                    Circle().fill(active ? color : Color(nsColor: .controlBackgroundColor))
                        .frame(width: active ? 11 : 9, height: active ? 11 : 9)
                        .overlay(Circle().stroke(color, lineWidth: active ? 2 : 1))
                        .padding(.top, 4)
                }
                .frame(width: 12, height: 52)
                VStack(alignment: .leading, spacing: 4) {
                    Text(pin.label)
                        .font(.caption.weight(active ? .bold : .medium))
                        .foregroundStyle(active ? color : .primary)
                        .multilineTextAlignment(.leading)
                    Text("after #\(pin.displaySeq) · \(messageTime(pin.createdAt))")
                        .font(.caption2).foregroundStyle(.tertiary)
                }
            }
            .padding(.horizontal, 14)
            .frame(maxWidth: .infinity, minHeight: 52, alignment: .leading)
            .contentShape(Rectangle())
            .background(active ? color.opacity(0.08) : .clear)
        }
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity)
        .contentShape(Rectangle())
        .contextMenu {
            Button("구간 핀 삭제", role: .destructive) { deletePin(pin) }
        }
    }

    private func bookmarkRow(_ bookmark: MessageBookmark) -> some View {
        Button { jumpBookmark(bookmark) } label: {
            VStack(alignment: .leading, spacing: 3) {
                Text(bookmark.label).font(.caption.bold()).foregroundStyle(.primary)
                Text("#\(bookmark.displaySeq) · \(messageTime(bookmark.createdAt))")
                    .font(.caption2).foregroundStyle(.tertiary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .contextMenu {
            Button("메시지 북마크 삭제", role: .destructive) {
                deleteBookmark(bookmark)
            }
        }
    }
}

private struct TimelinePinPositionPreferenceKey: PreferenceKey {
    static let defaultValue: [String: CGFloat] = [:]

    static func reduce(value: inout [String: CGFloat], nextValue: () -> [String: CGFloat]) {
        value.merge(nextValue(), uniquingKeysWith: { _, new in new })
    }
}

private struct AttentionCard: View {
    let request: AttentionRequest
    let answer: () -> Void
    var body: some View {
        let isAmbient = request.pmRelation == "ambient"
        HStack(spacing: 12) {
            Text(isAmbient ? "OBSERVE" : request.replyLevel == "r3" ? "PM CONFIRM" : request.replyLevel == "r2" ? "REVIEW" : "INFO")
                .font(.caption2.bold()).padding(6)
                .background(isAmbient ? Color.gray : request.replyLevel == "r3" ? Color.red : Color.orange, in: RoundedRectangle(cornerRadius: 5))
                .foregroundStyle(.white)
            VStack(alignment: .leading) {
                Text(request.senderName).font(.caption).foregroundStyle(.secondary)
                Text(MessagePrettyPrinter.prettyText(request.body, seed: request.seq))
                    .lineLimit(2)
            }
            Button("Answer", action: answer)
        }.padding(12).frame(width: 430, alignment: .leading)
            .background((isAmbient ? Color.gray : Color.orange).opacity(0.09), in: RoundedRectangle(cornerRadius: 11))
            .overlay(RoundedRectangle(cornerRadius: 11).stroke((isAmbient ? Color.gray : Color.orange).opacity(0.25)))
    }
}

private struct MessageRow: View {
    let message: ChatMessage
    let pmID: String
    let pmProfile: PMProfile
    let roles: [WorkspaceRole]
    /// 발신자 principal id → 그가 lead 인 방 이름. HQ 타임라인에서만 실질이 있다.
    let leadRooms: [String: String]
    let isBookmarked: Bool
    let selectContext: (String) -> Void
    let bookmark: () -> Void
    @State private var showPretty = true
    /// 정형 안내문(init·lead 지정)은 기본 접힘. 매번 전문이 펼쳐져 있으면
    /// 타임라인에서 사람 말이 밀려난다.
    @State private var expanded = false

    private static let boilerplateTags: Set<String> = [
        "fungis-init", "onboarding", "lead-notice",
    ]
    private var isBoilerplate: Bool {
        !Self.boilerplateTags.isDisjoint(with: message.tags)
    }

    var body: some View {
        HStack(alignment: .bottom, spacing: 9) {
            if isMine { Spacer(minLength: 100) }

            if !isMine { senderAvatar }

            VStack(alignment: isMine ? .trailing : .leading, spacing: 5) {
                HStack(spacing: 6) {
                    Text(senderLabel).font(.caption.bold())
                    if let role = senderRole, !isMine {
                        Text(role.name).font(.caption2).foregroundStyle(.blue)
                    }
                    Text(messageTime(message.createdAt))
                        .font(.caption2).foregroundStyle(.tertiary)
                }

                if hasContextMetadata { contextMetadata }

                Group {
                    if isBoilerplate && !expanded {
                        HStack(spacing: 6) {
                            Text(message.body.split(
                                separator: "\n", maxSplits: 1,
                                omittingEmptySubsequences: false
                            ).first.map(String.init) ?? message.body)
                                .lineLimit(1)
                            Button("펼치기") { expanded = true }
                                .buttonStyle(.plain).font(.caption)
                                .foregroundStyle(.secondary)
                                .contentShape(Rectangle())
                        }
                    } else if showPretty {
                        Text(MessagePrettyPrinter.prettyText(message.body, seed: message.seq))
                    } else {
                        Text(message.body)
                    }
                }
                    .font(.body).lineSpacing(3).textSelection(.enabled)
                    // 원문과 Pretty는 줄 수가 다르다. 뒤집힌 LazyVStack 안에서는
                    // 바뀐 높이가 다시 제안되지 않아 아래가 잘린다. Text가 자기
                    // 높이를 그대로 말하게 둔다.
                    .fixedSize(horizontal: false, vertical: true)
                    .foregroundStyle(isMine ? Color.white : Color.primary)
                    .padding(.horizontal, 14).padding(.vertical, 11)
                    .background(bubbleColor, in: RoundedRectangle(cornerRadius: 16))
                    .overlay {
                        if !isMine {
                            RoundedRectangle(cornerRadius: 16)
                                .stroke(Color.secondary.opacity(0.12), lineWidth: 1)
                        }
                    }

                HStack(spacing: 7) {
                    Button(action: bookmark) {
                        Image(systemName: isBookmarked ? "bookmark.fill" : "bookmark")
                            .frame(width: 22, height: 22)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(isBookmarked ? .orange : .secondary)
                    .help("타임라인 북마크 추가")
                    Button {
                        showPretty.toggle()
                    } label: {
                        Label(
                            showPretty ? "원문 보기" : "Pretty 보기",
                            systemImage: showPretty ? "doc.plaintext" : "text.alignleft"
                        )
                        .padding(.vertical, 3).padding(.horizontal, 2)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(.blue)
                    Text(recipientSummary).lineLimit(1)
                    if hasUndeliveredRole {
                        Label("전달 대기", systemImage: "clock")
                            .foregroundStyle(.orange)
                    }
                    if let relation = message.pmRelation, relation != "self" {
                        Text(relationLabel(relation)).fontWeight(.semibold)
                            .foregroundStyle(relationColor(relation))
                    }
                    Text("#\(message.displaySeq)").foregroundStyle(.quaternary)
                }.font(.caption2).foregroundStyle(.secondary)
            }
            .frame(maxWidth: 680, alignment: isMine ? .trailing : .leading)

            if isMine { senderAvatar }
            if !isMine { Spacer(minLength: 100) }
        }.frame(maxWidth: .infinity)
            .padding(.vertical, 5)
    }

    private var isMine: Bool { message.senderID == pmID }

    @ViewBuilder private var senderAvatar: some View {
        if isMine {
            PMAvatar(profile: pmProfile, size: 34)
        } else if let role = senderRole {
            RoleAvatar(role: role, size: 34)
        } else if let room = leadRooms[message.senderID] {
            // 그 방의 lead 다. 방 아바타를 입힌다 — 누구의 말인지가 아니라
            // 어느 방의 말인지가 HQ 에서 읽는 사람이 알고 싶은 것이다.
            ZStack {
                Circle().fill(roleAvatarColor(room))
                Text(roleInitials(room))
                    .font(.system(size: 13, weight: .bold, design: .rounded))
                    .foregroundStyle(.white)
            }.frame(width: 34, height: 34)
        } else {
            ZStack {
                Circle().fill(Color.secondary.opacity(0.13))
                Text(String(message.senderName.prefix(2)).uppercased())
                    .font(.caption2.bold()).foregroundStyle(.secondary)
            }.frame(width: 34, height: 34)
        }
    }

    private var senderLabel: String {
        isMine ? pmProfile.displayName : message.senderName
    }

    private var bubbleColor: Color {
        isMine ? Color.accentColor : Color(nsColor: .controlBackgroundColor)
    }

    private var hasContextMetadata: Bool {
        message.track != nil || !message.tags.isEmpty
            || !message.detectedContexts.isEmpty || message.inReplyTo != nil
    }

    private var contextMetadata: some View {
        HStack(spacing: 6) {
            if let parent = message.inReplyToProjectSeq ?? message.inReplyTo {
                Text("↳ #\(parent)").font(.caption2).foregroundStyle(.tertiary)
            }
            if let track = message.track {
                ContextButton(label: track, color: contextColor(track), icon: "point.topleft.down.to.point.bottomright.curvepath") {
                    selectContext(track)
                }
            }
            ForEach(message.tags, id: \.self) { tag in
                ContextButton(label: tag, color: contextColor(tag), icon: "tag") {
                    selectContext(tag)
                }
            }
            ForEach(message.detectedContexts, id: \.self) { detected in
                DetectedContextButton(context: detected) { selectContext(detected.value) }
            }
        }
    }

    private var senderRole: WorkspaceRole? {
        roles.first { $0.agentID == message.senderID }
    }

    private var hasUndeliveredRole: Bool {
        message.roleRecipients.contains { $0.deliveredAgentID == nil }
    }

    private var recipientSummary: String {
        let roleNames = message.roleRecipients.map { "@\($0.name)" }
        let roleAgentIDs = Set(message.roleRecipients.compactMap(\.deliveredAgentID))
        // HQ 는 수신자를 lead 의 agent id 로 풀어서 저장한다. 역할이 없으니
        // 그대로 두면 to 가 claude-난수로 찍힌다. 발신자 아바타와 같은 지도로
        // 방 이름을 입힌다 — 여기서도 알고 싶은 것은 누구냐가 아니라 어느 방이냐다.
        let directNames = message.recipients
            .filter { !roleAgentIDs.contains($0.recipientID) }
            .map { leadRooms[$0.recipientID] ?? $0.displayName }
        let names = roleNames + directNames
        let head = names.isEmpty ? "수신자 없음" : "to: " + names.joined(separator: ", ")
        // 참조도 배달되므로 누가 듣고 있었는지가 기록의 일부다. 안 보여주면
        // 나중에 "왜 저 사람이 이 얘기를 알지"를 되짚을 수 없다.
        guard !message.references.isEmpty else { return head }
        let copied = message.references.map { reference in
            roles.first { $0.agentID == reference.principalID }
                .map { "@\($0.name)" }
                ?? leadRooms[reference.principalID]
                ?? reference.displayName
        }
        return head + "  ·  cc: " + copied.joined(separator: ", ")
    }

    private func relationLabel(_ relation: String) -> String {
        switch relation {
        case "confirm": "CONFIRM"
        case "direct": "TO PM"
        case "reference": "CC PM"
        default: "AMBIENT"
        }
    }

    private func relationColor(_ relation: String) -> Color {
        switch relation {
        case "confirm": .red
        case "direct": .orange
        case "reference": .blue
        default: .secondary
        }
    }
}

private func messageTime(_ value: String) -> String {
    let parser = ISO8601DateFormatter()
    parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    guard let date = parser.date(from: value) else { return value }
    let calendar = Calendar.current
    let formatter = DateFormatter()
    formatter.locale = Locale.current
    formatter.timeZone = .current
    formatter.dateFormat = calendar.isDateInToday(date) ? "HH:mm" : "M/d HH:mm"
    return formatter.string(from: date)
}

private struct ContextFilterChip: View {
    let label: String
    let selected: Bool
    let color: Color
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Text(label)
                .font(.caption.bold()).lineLimit(1).truncationMode(.middle)
                .frame(maxWidth: 260)
                .padding(.horizontal, 8).padding(.vertical, 5)
                .contentShape(Capsule())
        }
            .buttonStyle(.plain)
            .foregroundStyle(selected ? .white : color)
            .background(selected ? color : color.opacity(0.1), in: Capsule())
            .help(label)
    }
}

private struct ContextTagTray: View {
    let contexts: [String]
    let selected: String?
    let select: (String?) -> Void
    @State private var expanded = false

    var body: some View {
        Group {
            if expanded {
                LazyVGrid(
                    columns: [GridItem(.adaptive(minimum: 96, maximum: 260), spacing: 8, alignment: .leading)],
                    alignment: .leading, spacing: 8
                ) {
                    allChip
                    ForEach(contexts, id: \.self) { context in contextChip(context) }
                    ContextTrayControl(label: "접기", systemImage: "chevron.up") {
                        expanded = false
                    }
                }
            } else {
                ViewThatFits(in: .horizontal) {
                    fullRow
                    collapsedRow(limit: 12)
                    collapsedRow(limit: 10)
                    collapsedRow(limit: 8)
                    collapsedRow(limit: 6)
                    collapsedRow(limit: 4)
                    collapsedRow(limit: 2)
                    collapsedRow(limit: 0)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var allChip: some View {
        ContextFilterChip(label: "All", selected: selected == nil, color: .secondary) {
            select(nil)
        }
    }

    private func contextChip(_ context: String) -> some View {
        ContextFilterChip(
            label: context, selected: selected == context,
            color: contextColor(context)
        ) { select(context) }
    }

    private var fullRow: some View {
        HStack(spacing: 8) {
            allChip
            ForEach(contexts, id: \.self) { context in contextChip(context) }
        }
    }

    private func collapsedRow(limit: Int) -> some View {
        HStack(spacing: 8) {
            allChip
            ForEach(Array(contexts.prefix(limit)), id: \.self) { context in
                contextChip(context)
            }
            ContextTrayControl(label: "+", systemImage: nil) {
                expanded = true
            }
        }
    }
}

private struct ContextTrayControl: View {
    let label: String
    let systemImage: String?
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Group {
                if let systemImage {
                    Label(label, systemImage: systemImage)
                } else {
                    Text(label)
                }
            }
            .padding(.vertical, 4).padding(.horizontal, 3)
            .contentShape(Rectangle())
        }
        .font(.caption.bold())
        .buttonStyle(.plain)
        .foregroundStyle(.secondary)
        .padding(.horizontal, 10).padding(.vertical, 5)
        .background(Color.secondary.opacity(0.1), in: Capsule())
    }
}

private struct ContextButton: View {
    let label: String
    let color: Color
    let icon: String
    let action: () -> Void
    var body: some View {
        // 여백을 버튼 바깥에 두면 캡슐은 커 보여도 글자만 눌린다. 안으로
        // 넣고 도형을 준다.
        Button(action: action) {
            Label(label, systemImage: icon).font(.caption2.bold())
                .padding(.horizontal, 6).padding(.vertical, 3)
                .contentShape(Capsule())
        }
            .buttonStyle(.plain).foregroundStyle(color)
            .background(color.opacity(0.1), in: Capsule())
    }
}

private struct DetectedContextButton: View {
    let context: DetectedContext
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: 3) {
                Image(systemName: context.verified ? "checkmark.seal" : "sparkle.magnifyingglass")
                Text("\(context.kind):\(context.value)")
            }.font(.caption2)
                .padding(.horizontal, 6).padding(.vertical, 3)
                .contentShape(Capsule())
        }.buttonStyle(.plain)
            .foregroundStyle(context.verified ? Color.green : Color.secondary)
            .overlay(Capsule().stroke(style: StrokeStyle(lineWidth: 1, dash: [3, 2])))
            .help(context.verified ? "Detected and verified from Git" : "Detected candidate; message metadata was not changed")
    }
}

private func contextColor(_ value: String) -> Color {
    let palette: [Color] = [.blue, .purple, .teal, .orange, .pink, .indigo, .green]
    let total = value.unicodeScalars.reduce(0) { $0 + Int($1.value) }
    return palette[total % palette.count]
}
