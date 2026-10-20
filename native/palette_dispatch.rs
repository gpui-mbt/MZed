// SPDX-License-Identifier: GPL-3.0-or-later

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct DispatchOwner<W, E> {
    pub window: W,
    pub workspace: Option<E>,
    pub document: Option<E>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SelectedCommand {
    OpenSettingsFile,
    ToggleFullScreen,
}

pub struct DispatchTicket<W, E> {
    owner: DispatchOwner<W, E>,
    command: Option<SelectedCommand>,
}

impl<W: PartialEq, E: PartialEq> DispatchTicket<W, E> {
    pub fn new(owner: DispatchOwner<W, E>, action: Option<&str>) -> Self {
        let command = match action {
            Some("mzed.open-settings-file") => Some(SelectedCommand::OpenSettingsFile),
            Some("mzed.toggle-full-screen") => Some(SelectedCommand::ToggleFullScreen),
            _ => None,
        };
        Self { owner, command }
    }

    pub fn take(
        &mut self,
        live_owner: Option<&DispatchOwner<W, E>>,
        admitted: bool,
    ) -> Option<SelectedCommand> {
        // A refused request must not become runnable if its old owner returns.
        let command = self.command.take();
        if admitted && self.owner.workspace.is_some() && live_owner == Some(&self.owner) {
            command
        } else {
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn owner() -> DispatchOwner<u64, u64> {
        DispatchOwner { window: 1, workspace: Some(2), document: Some(3) }
    }

    fn settings() -> DispatchTicket<u64, u64> {
        DispatchTicket::new(owner(), Some("mzed.open-settings-file"))
    }

    #[test]
    fn matching_live_document_dispatches_once() {
        let mut ticket = settings();
        assert_eq!(ticket.take(Some(&owner()), true), Some(SelectedCommand::OpenSettingsFile));
        assert_eq!(ticket.take(Some(&owner()), true), None);
    }

    #[test]
    fn closed_or_replaced_document_workspace_and_window_cannot_dispatch() {
        for current in [
            None,
            Some(DispatchOwner { window: 4, ..owner() }),
            Some(DispatchOwner { workspace: Some(4), ..owner() }),
            Some(DispatchOwner { document: Some(4), ..owner() }),
            Some(DispatchOwner { document: None, ..owner() }),
        ] {
            let mut ticket = settings();
            assert_eq!(ticket.take(current.as_ref(), true), None);
            assert_eq!(ticket.take(Some(&owner()), true), None);
        }
    }

    #[test]
    fn stale_selection_or_inactive_window_is_terminal() {
        let mut ticket = settings();
        assert_eq!(ticket.take(Some(&owner()), false), None);
        assert_eq!(ticket.take(Some(&owner()), true), None);
    }

    #[test]
    fn escape_and_unknown_actions_never_dispatch() {
        for action in [None, Some("unknown")] {
            let mut ticket = DispatchTicket::new(owner(), action);
            assert_eq!(ticket.take(Some(&owner()), true), None);
        }
    }
}
