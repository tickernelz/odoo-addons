/** Unified inbox for Email Client for Odoo Full.
 *
 * Written against the subset of OWL shared by Odoo 18/19 (OWL 2) and Odoo 20 (OWL 3):
 * templates read everything through `this.`, no static props, no refs, reactive state via
 * useState (OWL 2) or proxy (OWL 3). Everything the user is allowed to do is decided on the
 * server (mailbox role); the UI only hides what would be refused anyway.
 */
import * as owl from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { user } from "@web/core/user";

const { Component, onWillStart, onWillUnmount } = owl;
const reactive = (obj) => (owl.useState ? owl.useState(obj) : owl.proxy(obj));
const PAGE = 40;

const ROLE_LABELS = {
    inbox: _t("Inbox"),
    sent: _t("Sent"),
    drafts: _t("Drafts"),
    archive: _t("Archived"),
    spam: _t("Spam"),
    trash: _t("Trash"),
};

export class UmcInbox extends Component {
    static template = "kaitech_email_client.Inbox";

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");
        this.bus = this.env.services.bus_service;
        const props = this.props || (this.__owl__ && this.__owl__.props) || {};
        const ctx = (props.action && props.action.context) || {};
        this.roleLabels = ROLE_LABELS;
        this.unifiedRoles = ["inbox", "sent", "archive", "spam", "trash"];
        this.locale = (user.lang || navigator.language || "en-US").replace("_", "-");
        this.state = reactive({
            overview: null,
            sel: {
                mailboxId: false,
                role: "inbox",
                folderId: false,
                partnerId: ctx.umc_partner_id || false,
                partnerName: ctx.umc_partner_name || "",
            },
            search: "",
            unreadOnly: false,
            listFilter: "all",
            tagId: false,
            tags: [],
            linkModels: [],
            tagEdit: false,
            newTag: "",
            link: { open: false, mode: "link", model: "", query: "", results: [] },
            threads: [],
            total: 0,
            loading: false,
            current: null,
            opening: false,
            syncing: false,
            expanded: {},
            notifyAsk: typeof Notification !== "undefined" && Notification.permission === "default",
        });
        this._searchTimer = null;
        this._unsubs = [];
        onWillStart(async () => {
            await Promise.all([this.loadOverview(), this.loadThreads(), this.loadTags(), this.loadLinkModels()]);
        });
        if (this.bus && this.bus.subscribe) {
            const sub = (type, cb) => {
                const res = this.bus.subscribe(type, cb);
                this._unsubs.push(typeof res === "function" ? res : () => this.bus.unsubscribe(type, cb));
            };
            sub("umc.counters", () => this.loadOverview());
            sub("umc.new_mail", (payload) => this.onNewMail(payload));
        }
        onWillUnmount(() => {
            this._unsubs.forEach((fn) => fn());
            clearTimeout(this._searchTimer);
            clearTimeout(this._linkTimer);
        });
    }

    // ------------------------------------------------------------------ getters
    get mailboxes() {
        return (this.state.overview && this.state.overview.mailboxes) || [];
    }
    get title() {
        const s = this.state.sel;
        if (s.partnerId) {
            return s.partnerName;
        }
        if (s.folderId) {
            const f = this.findFolder(s.folderId);
            return f ? f.name : "";
        }
        const label = ROLE_LABELS[s.role] || s.role;
        if (s.mailboxId) {
            const box = this.mailboxes.find((b) => b.id === s.mailboxId);
            return box ? `${label} · ${box.name}` : label;
        }
        return s.role === "inbox" ? _t("All inboxes") : label;
    }
    get canSend() {
        return this.mailboxes.some((b) => b.role === "reply" || b.role === "manage");
    }
    get currentCanReply() {
        const c = this.state.current;
        return c && (c.role === "reply" || c.role === "manage");
    }
    findFolder(id) {
        for (const b of this.mailboxes) {
            const f = b.folders.find((x) => x.id === id);
            if (f) {
                return f;
            }
        }
        return null;
    }
    unifiedLabel(role) {
        return role === "inbox" ? _t("All inboxes") : ROLE_LABELS[role];
    }
    unifiedCount(role) {
        const u = (this.state.overview && this.state.overview.unified) || {};
        return u[role] || 0;
    }
    isActiveUnified(role) {
        const s = this.state.sel;
        return !s.partnerId && !s.mailboxId && !s.folderId && s.role === role;
    }
    isActiveFolder(folder) {
        return !this.state.sel.partnerId && this.state.sel.folderId === folder.id;
    }

    // ------------------------------------------------------------------ data
    async loadOverview() {
        this.state.overview = await this.orm.call("umc.mailbox", "get_inbox_overview", []);
    }

    async loadThreads(more = false) {
        const s = this.state.sel;
        this.state.loading = true;
        try {
            const res = await this.orm.call("umc.thread", "umc_list", [], {
                mailbox_id: s.mailboxId,
                role: s.role,
                folder_id: s.folderId,
                partner_id: s.partnerId,
                search: this.state.search,
                unread_only: this.state.unreadOnly,
                list_filter: this.state.listFilter,
                tag_id: this.state.tagId,
                offset: more ? this.state.threads.length : 0,
                limit: PAGE,
            });
            this.state.threads = more ? this.state.threads.concat(res.threads) : res.threads;
            this.state.total = res.total;
        } finally {
            this.state.loading = false;
        }
    }

    async openThread(thread) {
        this.state.opening = true;
        this.state.tagEdit = false;
        this.state.link.open = false;
        try {
            this.state.current = await this.orm.call("umc.thread", "umc_open", [thread.id]);
            const row = this.state.threads.find((t) => t.id === thread.id);
            if (row) {
                row.unread = 0;
            }
        } finally {
            this.state.opening = false;
        }
    }

    async loadTags() {
        this.state.tags = await this.orm.call("umc.thread", "umc_tags", []);
    }

    async loadLinkModels() {
        this.state.linkModels = await this.orm.call("umc.thread", "umc_link_models", []);
    }

    // ------------------------------------------------------------------ navigation
    select(sel) {
        Object.assign(this.state.sel, { mailboxId: false, role: "inbox", folderId: false, partnerId: false }, sel);
        this.state.tagId = false;
        this.state.current = null;
        this.loadThreads();
    }
    selectTag(tag) {
        Object.assign(this.state.sel, { mailboxId: false, role: "inbox", folderId: false, partnerId: false });
        this.state.tagId = tag.id;
        this.state.current = null;
        this.loadThreads();
    }
    isActiveTag(tag) {
        return this.state.tagId === tag.id;
    }
    setFilter(value) {
        this.state.listFilter = value;
        this.state.unreadOnly = false;
        this.loadThreads();
    }
    get listFilters() {
        return [
            ["all", _t("All")],
            ["unread", _t("Unread")],
            ["starred", _t("Starred")],
            ["in", _t("Incoming")],
            ["out", _t("Outgoing")],
        ];
    }
    tagStyle(tag) {
        const hue = ((tag.color || 0) * 47) % 360;
        return `background-color: hsl(${hue}, 60%, 88%); color: hsl(${hue}, 60%, 25%);`;
    }

    // ------------------------------------------------------------------ tags of the open conversation
    hasTag(tag) {
        const c = this.state.current;
        return Boolean(c && c.thread.tags.some((t) => t.id === tag.id));
    }
    async toggleTag(tag) {
        const c = this.state.current;
        const ids = c.thread.tags.map((t) => t.id);
        const next = ids.includes(tag.id) ? ids.filter((id) => id !== tag.id) : ids.concat([tag.id]);
        c.thread.tags = await this.orm.call("umc.thread", "umc_set_tags", [[c.thread.id], next]);
        await this.loadThreads();
    }
    toggleTagEdit() {
        this.state.tagEdit = !this.state.tagEdit;
    }
    onNewTagInput(ev) {
        this.state.newTag = ev.target.value;
    }
    onNewTagKey(ev) {
        if (ev.key === "Enter") {
            this.addTag();
        }
    }
    async addTag() {
        const name = (this.state.newTag || "").trim();
        if (!name) {
            return;
        }
        let tag = this.state.tags.find((t) => t.name.toLowerCase() === name.toLowerCase());
        if (!tag) {
            const [id] = await this.orm.create("umc.tag", [{ name }]);
            await this.loadTags();
            tag = this.state.tags.find((t) => t.id === id);
        }
        this.state.newTag = "";
        if (!this.hasTag(tag)) {
            await this.toggleTag(tag);
        }
    }

    // ------------------------------------------------------------------ records linked to the open conversation
    get linkableModels() {
        return this.state.linkModels;
    }
    openLink(mode = "link") {
        const first = this.state.linkModels[0];
        this.state.link = {
            open: true,
            mode,
            model: mode === "task" ? "project.project" : first ? first.model : "",
            query: "",
            results: [],
        };
        this.searchLink();
    }
    closeLink() {
        this.state.link.open = false;
    }
    onLinkModel(ev) {
        this.state.link.model = ev.target.value;
        this.searchLink();
    }
    onLinkQuery(ev) {
        this.state.link.query = ev.target.value;
        clearTimeout(this._linkTimer);
        this._linkTimer = setTimeout(() => this.searchLink(), 250);
    }
    async searchLink() {
        const l = this.state.link;
        if (!l.model) {
            l.results = [];
            return;
        }
        l.results = await this.orm.call(l.model, "name_search", [], { name: l.query, limit: 8 });
    }
    async pickLink(result) {
        const c = this.state.current;
        const l = this.state.link;
        if (l.mode === "task") {
            const res = await this.orm.call("umc.thread", "umc_create_record", [c.thread.id, "project.task", result[0]]);
            c.links = res.links;
            this.notification.add(_t("Task created: %s", res.record.name), { type: "success" });
        } else {
            c.links = await this.orm.call("umc.thread", "umc_link", [c.thread.id, l.model, result[0]]);
        }
        l.open = false;
    }
    async createLead() {
        const c = this.state.current;
        const res = await this.orm.call("umc.thread", "umc_create_record", [c.thread.id, "crm.lead"]);
        c.links = res.links;
        this.notification.add(_t("Lead created: %s", res.record.name), { type: "success" });
    }
    canCreate(model) {
        return this.state.linkModels.some((m) => m.model === model && m.create);
    }
    async removeLink(link) {
        const c = this.state.current;
        c.links = await this.orm.call("umc.thread", "umc_unlink", [link.id]);
    }
    async makePrimary(link) {
        const c = this.state.current;
        c.links = await this.orm.call("umc.thread", "umc_link", [c.thread.id, link.model, link.res_id, true]);
    }
    openRecord(link) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: link.model,
            res_id: link.res_id,
            views: [[false, "form"]],
            target: "current",
        });
    }
    selectUnified(role) {
        this.select({ role });
    }
    selectFolder(box, folder) {
        this.select({ mailboxId: box.id, folderId: folder.id, role: folder.role });
    }
    toggleBox(box) {
        this.state.expanded[box.id] = !this.state.expanded[box.id];
    }
    onSearchInput(ev) {
        this.state.search = ev.target.value;
        clearTimeout(this._searchTimer);
        this._searchTimer = setTimeout(() => this.loadThreads(), 300);
    }
    back() {
        this.state.current = null;
    }

    // ------------------------------------------------------------------ actions
    async sync() {
        this.state.syncing = true;
        try {
            const res = await this.orm.call("umc.mailbox", "umc_sync_now", [[]]);
            if (res.errors && res.errors.length) {
                this.notification.add(res.errors.join("\n"), { type: "danger", sticky: true });
            } else {
                this.notification.add(
                    res.new ? _t("%s new messages", res.new) : _t("Everything is up to date"),
                    { type: "success" }
                );
            }
            await Promise.all([this.loadOverview(), this.loadThreads()]);
        } finally {
            this.state.syncing = false;
        }
    }

    async act(action) {
        const c = this.state.current;
        if (!c) {
            return;
        }
        await this.orm.call("umc.thread", "umc_mark", [[c.thread.id], action]);
        if (["archive", "trash", "spam", "inbox", "unread"].includes(action)) {
            this.state.current = null;
        } else if (action === "star" || action === "unstar") {
            c.thread.starred = action === "star";
        }
        await Promise.all([this.loadOverview(), this.loadThreads()]);
    }

    compose(mode = "new", message = null) {
        const context = { umc_mode: mode };
        if (message) {
            context.umc_message_id = message.id;
        } else if (this.state.sel.mailboxId) {
            context.umc_mailbox_id = this.state.sel.mailboxId;
        }
        this.actionService.doAction(
            {
                type: "ir.actions.act_window",
                name: {
                    new: _t("New message"),
                    reply: _t("Reply"),
                    reply_all: _t("Reply all"),
                    forward: _t("Forward"),
                }[mode],
                res_model: "umc.composer",
                views: [[false, "form"]],
                target: "new",
                context,
            },
            {
                onClose: async () => {
                    const open = this.state.current;
                    await this.loadThreads();
                    if (open) {
                        await this.openThread(open.thread);
                    }
                },
            }
        );
    }

    openPartner(partner) {
        if (!partner) {
            return;
        }
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "res.partner",
            res_id: partner.id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openMailboxes() {
        this.actionService.doAction("kaitech_email_client.action_umc_mailbox");
    }

    async askNotifications() {
        if (typeof Notification !== "undefined") {
            await Notification.requestPermission();
        }
        this.state.notifyAsk = false;
    }

    onNewMail(payload) {
        this.loadThreads();
        const title = payload.count > 1 ? _t("%s new messages", payload.count) : payload.from;
        const body = `${payload.mailbox} · ${payload.subject || ""}`;
        if (typeof Notification !== "undefined" && Notification.permission === "granted" && document.hidden) {
            const n = new Notification(title, { body, tag: `umc-${payload.mailbox_id}` });
            n.onclick = () => window.focus();
        } else {
            this.notification.add(body, { title, type: "info" });
        }
    }

    // ------------------------------------------------------------------ rendering helpers
    fmtDate(value, long = false) {
        if (!value) {
            return "";
        }
        const d = new Date(value.replace(" ", "T") + "Z");
        const now = new Date();
        if (long) {
            return d.toLocaleString(this.locale, { dateStyle: "medium", timeStyle: "short" });
        }
        if (d.toDateString() === now.toDateString()) {
            return d.toLocaleTimeString(this.locale, { hour: "2-digit", minute: "2-digit" });
        }
        if (d.getFullYear() === now.getFullYear()) {
            return d.toLocaleDateString(this.locale, { day: "numeric", month: "short" });
        }
        return d.toLocaleDateString(this.locale);
    }
    initial(text) {
        return ((text || "?").trim()[0] || "?").toUpperCase();
    }
    avatarColor(text) {
        let h = 0;
        for (const ch of text || "") {
            h = (h * 31 + ch.charCodeAt(0)) % 360;
        }
        return `background-color: hsl(${h}, 45%, 45%)`;
    }
    boxColor(box) {
        const palette = ["#6c757d", "#f06050", "#f4a460", "#f7cd1f", "#6cc1ed", "#814968", "#eb7e7f",
            "#2c8397", "#475577", "#d6145f", "#30c381", "#9365b8"];
        return `background-color: ${palette[(box && box.color) || 0] || palette[0]}`;
    }
    fileSize(bytes) {
        if (!bytes) {
            return "";
        }
        const units = ["B", "KB", "MB", "GB"];
        let i = 0;
        let n = bytes;
        while (n >= 1024 && i < units.length - 1) {
            n /= 1024;
            i++;
        }
        return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
    }
    frameDoc(msg) {
        const style =
            "<style>html,body{margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI'," +
            "Roboto,Helvetica,Arial,sans-serif;font-size:14px;line-height:1.5;color:#212529;" +
            "word-wrap:break-word;overflow-wrap:anywhere}img{max-width:100%;height:auto}" +
            "blockquote{margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex;color:#555}" +
            "pre{white-space:pre-wrap}table{max-width:100%}</style>";
        return `<!DOCTYPE html><html><head><meta charset="utf-8"><base target="_blank">${style}</head>` +
            `<body>${msg.body || ""}</body></html>`;
    }
    fitFrame(ev) {
        const frame = ev.target;
        try {
            const doc = frame.contentDocument;
            const height = Math.max(doc.body.scrollHeight, doc.documentElement.scrollHeight);
            frame.style.height = `${height + 8}px`;
        } catch {
            frame.style.height = "480px";
        }
    }
}

registry.category("actions").add("umc.inbox", UmcInbox);
