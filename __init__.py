from ovos_bus_client.session import SessionManager
from ovos_solver_openai_persona import OpenAIPersonaSolver
from ovos_utils import classproperty
from ovos_utils.process_utils import RuntimeRequirements
from ovos_workshop.skills.fallback import FallbackSkill


class ChatGPTSkill(FallbackSkill):
    sessions = {}

    @classproperty
    def runtime_requirements(self):
        return RuntimeRequirements(
            internet_before_load=True,
            network_before_load=True,
            requires_internet=True,
            requires_network=True
        )

    def initialize(self):
        self.add_event("speak", self.handle_speak)
        self.add_event("recognizer_loop:utterance", self.handle_utterance)
        self.register_fallback(self.ask_chatgpt, 85)

    @property
    def chat(self):
        """created fresh to allow key/url rotation when settings.json is edited"""
        return OpenAIPersonaSolver(config=self.settings)
        
    def handle_utterance(self, message):
        utt = message.data.get("utterances")[0]
        sess = SessionManager.get(message)
        if sess.session_id not in self.sessions:
            self.sessions[sess.session_id] = []
        self.sessions[sess.session_id].append(("user", utt))

    def handle_speak(self, message):
        utt = message.data.get("utterance")
        sess = SessionManager.get(message)
        if sess.session_id in self.sessions:
            self.sessions[sess.session_id].append(("ai", utt))

    def build_msg_history(self, message):
        sess = SessionManager.get(message)
        if sess.session_id not in self.sessions:
            return []
        messages = []  # tuple of question, answer

        q = None
        ans = None
        for m in self.sessions[sess.session_id]:
            if m[0] == "user":
                q = m[1]  # track question
                if ans is not None:
                    # save previous q/a pair
                    messages.append((q, ans))
                    q = None
                ans = None
            elif m[0] == "ai":
                if ans is None:
                    ans = m[1]  # track answer
                else:  # merge multi speak answers
                    ans = f"{ans}. {m[1]}"

        # save last q/a pair
        if ans is not None and q is not None:
            messages.append((q, ans))
        return messages

    def _async_ask(self, message):
        utterance = message.data["utterance"]
        self.chat.qa_pairs = self.build_msg_history(message)
        answered = False
        try:
            for utt in self.chat.stream_utterances(utterance):
                answered = True
                self.speak(utt)
        except: # speak error on any network issue / no credits etc
            pass
        if not answered:
            self.speak_dialog("gpt_error")

    def ask_chatgpt(self, message):
        if "key" not in self.settings:
            return False  # ChatGPT not configured yet
        utterance = message.data["utterance"]
        # ask in a thread so fallback doesnt timeout
        self.bus.once("async.chatgpt.fallback", self._async_ask)
        self.bus.emit(
            message.forward("async.chatgpt.fallback", {"utterance": utterance})
        )
        return True
