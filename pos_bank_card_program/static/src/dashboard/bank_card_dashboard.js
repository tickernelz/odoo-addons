import { Component, onWillStart, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { getColor } from "@web/core/colors/colors";
import { DateTimeInput } from "@web/core/datetime/datetime_input";
import { serializeDate } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";

const { DateTime } = luxon;

export class BankCardDashboard extends Component {
    static template = "pos_bank_card_program.BankCardDashboard";
    static components = { DateTimeInput, Layout };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.canvas = useRef("chart");
        this.state = useState({ data: null, bankId: false, programId: false, dateFrom: null, dateTo: null });
        this.chart = null;
        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this.load();
        });
        useEffect(
            () => this.renderChart(),
            () => [this.state.data]
        );
        onWillUnmount(() => this.chart?.destroy());
    }

    get programs() {
        const programs = this.state.data?.programs || [];
        return this.state.bankId ? programs.filter((p) => p.bank_id === this.state.bankId) : programs;
    }

    async load() {
        const data = await this.orm.call("pos.bank.card.dashboard", "get_dashboard", [], {
            date_from: this.state.dateFrom ? serializeDate(this.state.dateFrom) : false,
            date_to: this.state.dateTo ? serializeDate(this.state.dateTo) : false,
            bank_id: this.state.bankId,
            program_id: this.state.programId,
        });
        this.state.dateFrom = DateTime.fromISO(data.date_from);
        this.state.dateTo = DateTime.fromISO(data.date_to);
        this.state.data = data;
    }

    onBankChange(ev) {
        this.state.bankId = parseInt(ev.target.value) || false;
        if (this.state.programId && !this.programs.some((p) => p.id === this.state.programId)) {
            this.state.programId = false;
        }
        this.load();
    }

    onProgramChange(ev) {
        this.state.programId = parseInt(ev.target.value) || false;
        this.load();
    }

    onDateChange(field, value) {
        if (!value) {
            return;
        }
        this.state[field] = value;
        this.load();
    }

    progressClass(percent) {
        if (percent >= 100) {
            return "bg-danger";
        }
        return percent >= 80 ? "bg-warning" : "bg-success";
    }

    openQuota() {
        this.action.doAction("pos_bank_card_program.action_pos_bank_card_quota_usage");
    }

    openUsage() {
        this.action.doAction("pos_bank_card_program.action_pos_bank_card_usage");
    }

    isDark() {
        const color = getComputedStyle(document.body).backgroundColor.match(/\d+/g) || [255, 255, 255];
        const [r, g, b] = color.map(Number);
        return 0.299 * r + 0.587 * g + 0.114 * b < 128;
    }

    renderChart() {
        if (!this.canvas.el || !this.state.data) {
            return;
        }
        this.chart?.destroy();
        const scheme = this.isDark() ? "dark" : "light";
        const text = getComputedStyle(document.body).color;
        const grid = scheme === "dark" ? "rgba(255,255,255,0.1)" : "rgba(0,0,0,0.08)";
        const days = this.state.data.days;
        const currency = this.state.data.currency;
        this.chart = new Chart(this.canvas.el, {
            type: "bar",
            data: {
                labels: days.map((d) => d.label),
                datasets: [
                    {
                        label: _t("Discount"),
                        data: days.map((d) => d.discount),
                        backgroundColor: getColor(0, scheme),
                    },
                    {
                        label: _t("Bank Share"),
                        data: days.map((d) => d.bank_share),
                        backgroundColor: getColor(3, scheme),
                    },
                ],
            },
            options: {
                maintainAspectRatio: false,
                plugins: { legend: { labels: { color: text } } },
                scales: {
                    x: {
                        ticks: { color: text },
                        grid: { color: grid },
                        title: { display: true, text: _t("Day"), color: text },
                    },
                    y: {
                        beginAtZero: true,
                        ticks: { color: text },
                        grid: { color: grid },
                        title: { display: true, text: _t("Amount (%s)", currency), color: text },
                    },
                },
            },
        });
    }
}

registry.category("actions").add("pos_bank_card_program.dashboard", BankCardDashboard);
