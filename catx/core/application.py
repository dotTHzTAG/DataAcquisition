from __future__ import annotations

from catx.core.paths import APPLICATION_LOG
from catx.services.acquisition import AcquisitionService
from catx.services.application_log import configure_application_logger
from ui.acquisition_main import AcquisitionMainWindow
from ui.data_manager import DataManagerWindow


def create_main_window() -> AcquisitionMainWindow:
    acquisition_service = AcquisitionService()

    logger, log_handler = configure_application_logger(APPLICATION_LOG)

    def create_data_manager(
        project_path=None, acquisition_window=None
    ) -> DataManagerWindow:
        return DataManagerWindow(
            acquisition_service=acquisition_service,
            project_path=project_path,
            acquisition_window=acquisition_window,
        )

    return AcquisitionMainWindow(
        acquisition_service=acquisition_service,
        data_manager_factory=create_data_manager,
        logger=logger,
        log_handler=log_handler,
    )
