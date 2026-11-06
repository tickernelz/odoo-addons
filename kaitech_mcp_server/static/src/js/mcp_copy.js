odoo.define('kaitech_mcp_server.mcp_copy', function (require) {
"use strict";

var core = require('web.core');
var common = require('web.form_common');

var _t = core._t;

var FieldCopy = common.AbstractField.extend({
    template: 'kaitech_mcp_server.FieldCopy',
    events: {
        'click .o_mcp_copy': 'copy_value',
    },
    render_value: function () {
        var value = this.get('value') || '';
        this.$('.o_mcp_copy_value').text(value);
        this.$('.o_mcp_copy').toggleClass('hidden', !value);
    },
    copy_value: function (event) {
        event.preventDefault();
        var area = $('<textarea/>').val(this.get('value') || '')
            .css({position: 'fixed', top: 0, left: 0, opacity: 0}).appendTo(document.body);
        area[0].select();
        var copied = false;
        try {
            copied = document.execCommand('copy');
        } catch (error) {
            copied = false;
        }
        area.remove();
        this.do_notify(copied ? _t("Copied") : _t("Select the text and copy it with Ctrl+C"), '');
    },
});

core.form_widget_registry.add('mcp_copy', FieldCopy);

return FieldCopy;
});
