"""vSphere SOAP API client using pyVmomi for snapshot operations."""

import os
import ssl
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim


class SoapClient:
    """SOAP API client for vSphere operations not available in REST API."""

    def __init__(self, hostname=None):
        if hostname is None:
            hostname = os.environ.get('VCENTER_HOST')
        self.hostname = hostname
        self.username = os.environ.get('VCENTER_USER')
        self.password = os.environ.get('VCENTER_PASSWORD')
        self.insecure = os.environ.get('INSECURE', 'True').lower() in ('true', '1', 't')
        self.si = None

    def connect(self):
        """Connect to vCenter via SOAP."""
        if self.si is not None:
            return self.si
        ssl_context = ssl._create_unverified_context() if self.insecure else None
        self.si = SmartConnect(
            host=self.hostname,
            user=self.username,
            pwd=self.password,
            port=443,
            sslContext=ssl_context
        )
        return self.si

    def disconnect(self):
        """Disconnect from vCenter."""
        if self.si:
            Disconnect(self.si)
            self.si = None

    def _find_vm_by_name(self, vm_name):
        """Find VM object by name."""
        si = self.connect()
        content = si.RetrieveContent()
        vm_view = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.VirtualMachine], True
        )
        vms = vm_view.view
        vm_view.Destroy()
        for vm in vms:
            if vm.name.lower() == vm_name.lower():
                return vm
        return None

    def _find_vm_by_mo_id(self, mo_id):
        """Find VM object by managed object ID (e.g. vm-130)."""
        si = self.connect()
        content = si.RetrieveContent()
        vm_view = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.VirtualMachine], True
        )
        vms = vm_view.view
        vm_view.Destroy()
        for vm in vms:
            if vm._moId == mo_id:
                return vm
        return None

    def find_vm(self, vm_id):
        """Find VM by name or managed object ID."""
        if vm_id.startswith('vm-'):
            return self._find_vm_by_mo_id(vm_id)
        return self._find_vm_by_name(vm_id)

    def list_snapshots(self, vm_id):
        """List all snapshots for a VM."""
        vm = self.find_vm(vm_id)
        if vm is None:
            return None, f"Virtual machine '{vm_id}' not found"

        if vm.snapshot is None:
            return vm, []  # No snapshots

        results = []
        self._walk_snapshot_tree(vm.snapshot.rootSnapshotList, results)
        return vm, results

    def _walk_snapshot_tree(self, snapshots, results):
        """Recursively walk snapshot tree."""
        for snap in snapshots:
            results.append({
                'name': snap.name,
                'description': snap.description,
                'create_time': str(snap.createTime),
                'state': snap.state,
                'id': str(snap.id),
                'is_current': getattr(snap, 'isCurrentSnapshot', False),
            })
            if snap.childSnapshotList:
                self._walk_snapshot_tree(snap.childSnapshotList, results)

    def create_snapshot(self, vm_id, snapshot_name, description='', memory=True, quiesce=False):
        """Create a snapshot for a VM."""
        vm = self.find_vm(vm_id)
        if vm is None:
            return f"Virtual machine '{vm_id}' not found"

        task = vm.CreateSnapshot_Task(
            name=snapshot_name,
            description=description,
            memory=memory,
            quiesce=quiesce
        )
        return self._wait_for_task(task, f"creating snapshot '{snapshot_name}'")

    def delete_snapshot_by_name(self, vm_id, snapshot_name, remove_children=False):
        """Delete a snapshot by name for a VM."""
        vm = self.find_vm(vm_id)
        if vm is None:
            return f"Virtual machine '{vm_id}' not found"

        if vm.snapshot is None:
            return f"No snapshots found for VM {vm.name}"

        snap_obj = self._find_snapshot_by_name(vm.snapshot.rootSnapshotList, snapshot_name)
        if snap_obj is None:
            return f"Snapshot '{snapshot_name}' not found"

        task = snap_obj.snapshot.DeleteSnapshot_Task(removeChildren=remove_children)
        return self._wait_for_task(task, f"deleting snapshot '{snapshot_name}'")

    def revert_to_snapshot(self, vm_id, snapshot_name, suppress_power_on=True):
        """Revert a VM to a named snapshot. Powers the VM off first if it is running."""
        vm = self.find_vm(vm_id)
        if vm is None:
            return f"Virtual machine '{vm_id}' not found"

        if vm.snapshot is None:
            return f"No snapshots found for VM {vm.name}"

        snap_obj = self._find_snapshot_by_name(vm.snapshot.rootSnapshotList, snapshot_name)
        if snap_obj is None:
            return f"Snapshot '{snapshot_name}' not found"

        try:
            previous_state = str(vm.runtime.powerState)

            # A running or suspended VM must be powered off before reverting
            if vm.runtime.powerState in (
                vim.VirtualMachinePowerState.poweredOn,
                vim.VirtualMachinePowerState.suspended,
            ):
                power_off_result = self._wait_for_task(
                    vm.PowerOff_Task(),
                    f"powering off VM {vm.name} before reverting",
                )
                if power_off_result.startswith("Failed"):
                    return (
                        f"Failed reverting VM {vm.name} to snapshot '{snapshot_name}': "
                        f"VM could not be powered off ({power_off_result})"
                    )

            result = self._wait_for_task(
                snap_obj.snapshot.RevertToSnapshot_Task(suppressPowerOn=suppress_power_on),
                f"reverting VM {vm.name} to snapshot '{snapshot_name}'",
            )
            if result.startswith("Failed"):
                return result

            # Refresh the cached VM properties so the post-revert state is accurate
            vm.Reload()
            current_state = str(vm.runtime.powerState)
            note = (
                "Note: the VM is left powered off; power it on manually if needed."
                if suppress_power_on
                else "Note: suppress_power_on is False, so vSphere powers the VM on after the revert."
            )
            return (
                f"{result}. Power state before revert: {previous_state}. "
                f"Power state after revert: {current_state}. {note}"
            )
        except Exception as e:
            return f"Failed reverting VM {vm.name} to snapshot '{snapshot_name}': {e}"

    def _find_snapshot_by_name(self, snapshots, name):
        """Find snapshot object by name in snapshot tree."""
        for snap in snapshots:
            if snap.name == name:
                return snap
            if snap.childSnapshotList:
                result = self._find_snapshot_by_name(snap.childSnapshotList, name)
                if result:
                    return result
        return None

    def _wait_for_task(self, task, operation):
        """Wait for a vSphere task to complete."""
        while task.info.state not in [vim.TaskInfo.State.success, vim.TaskInfo.State.error]:
            pass
        if task.info.state == vim.TaskInfo.State.success:
            return f"Successfully completed {operation}"
        else:
            return f"Failed {operation}: {task.info.error}"